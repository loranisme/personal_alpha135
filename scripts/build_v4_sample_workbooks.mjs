import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const outputDir = process.argv[2];
if (!outputDir) throw new Error("OUTPUT_DIRECTORY_REQUIRED");

const fontFamily = "Arial";
const navy = "#17365D";
const blue = "#D9EAF7";
const amber = "#FFF2CC";
const red = "#FCE4D6";
const green = "#E2F0D9";

function excelColumn(n) {
  let result = "";
  while (n > 0) {
    n -= 1;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
}

function coerceMatrix(values) {
  return values.map((row, rowIndex) => row.map((value) => {
    if (rowIndex === 0 || value === null || value === undefined || value === "") return value ?? null;
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (/^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed)) return Number(trimmed);
    return value;
  }));
}

async function readCsvMatrix(relativePath, sheetName = "Import") {
  const csvText = await fs.readFile(path.join(outputDir, relativePath), "utf8");
  const imported = await Workbook.fromCSV(csvText, { sheetName });
  return coerceMatrix(imported.worksheets.getItem(sheetName).getUsedRange().values);
}

function styleTitle(sheet, title, subtitle) {
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: fontFamily, size: 15, bold: true, color: "#1F1F1F" };
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange("A3").format.font = { name: fontFamily, size: 10, italic: true, color: "#666666" };
}

function writeTable(sheet, matrix, startRow = 5) {
  const rows = matrix.length;
  const cols = Math.max(...matrix.map((row) => row.length));
  const padded = matrix.map((row) => [...row, ...Array(cols - row.length).fill(null)]);
  const range = sheet.getRangeByIndexes(startRow - 1, 0, rows, cols);
  range.values = padded;
  range.format.font = { name: fontFamily, size: 10, color: "#1F1F1F" };
  range.format.verticalAlignment = "center";
  const header = sheet.getRangeByIndexes(startRow - 1, 0, 1, cols);
  header.format.fill = navy;
  header.format.font = { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" };
  header.format.horizontalAlignment = "center";
  header.format.borders = { preset: "inside", style: "thin", color: "#FFFFFF" };
  range.format.autofitColumns();
  range.format.autofitRows();
  for (let col = 0; col < cols; col += 1) {
    const width = sheet.getRangeByIndexes(startRow - 1, col, rows, 1).format.columnWidth;
    if (width > 32) sheet.getRangeByIndexes(startRow - 1, col, rows, 1).format.columnWidth = 32;
  }
  sheet.freezePanes.freezeRows(startRow);
  return { rows, cols, range: `A${startRow}:${excelColumn(cols)}${startRow + rows - 1}` };
}

function addOverviewSheet(workbook, name, title, rows, note) {
  const sheet = workbook.worksheets.add(name);
  styleTitle(sheet, title, note);
  const matrix = [["项目", "结果"], ...rows];
  writeTable(sheet, matrix, 5);
  sheet.getRange(`A6:A${5 + rows.length}`).format.fill = blue;
  sheet.getRange(`A6:A${5 + rows.length}`).format.font = { name: fontFamily, size: 10, bold: true };
  sheet.getRange(`B6:B${5 + rows.length}`).format.columnWidth = 30;
  return sheet;
}

async function addCsvSheet(workbook, name, title, subtitle, relativePath) {
  const sheet = workbook.worksheets.add(name);
  styleTitle(sheet, title, subtitle);
  const matrix = await readCsvMatrix(relativePath, `${name}Import`);
  writeTable(sheet, matrix, 5);
  return sheet;
}

async function verifyAndExport(workbook, filename) {
  workbook.recalculate();
  const inspection = await workbook.inspect({ kind: "workbook,sheet,table", maxChars: 5000, tableMaxRows: 4, tableMaxCols: 8 });
  const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 100 }, summary: `${filename} formula error scan` });
  const previewDir = path.join(outputDir, "qa_previews", filename.replace(".xlsx", ""));
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheet of workbook.worksheets.items) {
    const used = sheet.getUsedRange().values;
    const previewRows = Math.min(30, used.length);
    const previewCols = Math.max(...used.slice(0, previewRows).map((row) => row.length));
    const previewRange = `A1:${excelColumn(previewCols)}${previewRows}`;
    const preview = await workbook.render({ sheetName: sheet.name, range: previewRange, scale: 1, format: "png" });
    await fs.writeFile(path.join(previewDir, `${sheet.name}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(path.join(outputDir, filename));
  return { inspection: inspection.ndjson, errorScan: errors.ndjson, sheets: workbook.worksheets.items.map((sheet) => sheet.name) };
}

const summary = JSON.parse(await fs.readFile(path.join(outputDir, "workflow_summary.json"), "utf8"));

const signalBook = Workbook.create();
addOverviewSheet(signalBook, "Overview", "V4 三 Alpha 信号审查", [
  ["工程状态", summary.engineering_workflow_status],
  ["因子库", summary.factor_library.version],
  ["候选因子", summary.factor_library.selected_factor_count],
  ["关联 BRAIN Alpha", summary.factor_library.selected_source_alpha_associations],
  ["数据源", summary.data.provider],
  ["股票数", summary.data.security_count],
  ["评估交易日", summary.data.evaluation_sessions],
  ["信号日期", String(summary.latest_selection.signal_session).slice(0, 10)],
  ["正式研究状态", summary.formal_research_status],
  ["发布状态", summary.release_status],
], "本文件展示冻结信号、目标权重和 Alphalens 诊断。结果属于开发暴露样例。");
const rankingSheet = await addCsvSheet(signalBook, "Ranking", "股票排名", "复合分数由三个因子的横截面百分位排名等权合成。", "latest_selection/ranking.csv");
rankingSheet.getRange("B6:B100").format.numberFormat = "0.0000";
const targetSheet = await addCsvSheet(signalBook, "Target Portfolio", "目标组合", "Top 10%，等权，最低现金 5%；股数按下一交易日历史开盘参考价计算。", "latest_selection/target_portfolio.csv");
targetSheet.getRange("C6:C100").format.numberFormat = "0.00%";
targetSheet.getRange("D6:D100").format.numberFormat = '"$"#,##0.00';
targetSheet.getRange("F6:F100").format.numberFormat = "0.00%";
const alphalensSheet = await addCsvSheet(signalBook, "Alphalens Summary", "Alphalens Reloaded 诊断", "包含三个单因子与复合因子的实际框架输出摘要。", "alphalens/summary.csv");
alphalensSheet.getRange("E6:F100").format.numberFormat = "0.0000";
const signalChecks = await addCsvSheet(signalBook, "Checks", "研究与发布检查", "BLOCKED 项目必须在正式发布前解决。", "latest_selection/checks.csv");
signalChecks.getRange("B6:B100").conditionalFormats.add("containsText", { text: "BLOCKED", format: { fill: red, font: { bold: true, color: "#9C0006" } } });
signalChecks.getRange("B6:B100").conditionalFormats.add("containsText", { text: "PARTIAL", format: { fill: amber, font: { bold: true, color: "#9C6500" } } });
const signalQa = await verifyAndExport(signalBook, "signal_review.xlsx");

const executionBook = Workbook.create();
addOverviewSheet(executionBook, "Overview", "V4 三 Alpha 执行与回测审查", [
  ["vectorbt 版本", summary.vectorbt.engine_version],
  ["回测起始", summary.vectorbt.start],
  ["回测结束", summary.vectorbt.end],
  ["工程总收益", summary.vectorbt.total_return],
  ["年化收益", summary.vectorbt.cagr],
  ["年化波动", summary.vectorbt.annualized_volatility],
  ["零利率 Sharpe", summary.vectorbt.sharpe_zero_rate],
  ["最大回撤", summary.vectorbt.max_drawdown],
  ["期末权益", summary.vectorbt.ending_equity],
  ["订单数", summary.vectorbt.engine_order_count],
  ["交易成本", summary.vectorbt.total_fees],
  ["正式发布", summary.release_status],
], "本文件展示历史工程回测和人工调仓草稿。实际提交订单数为 0。");
executionBook.worksheets.getItem("Overview").getRange("B9:B11").format.numberFormat = "0.00%";
executionBook.worksheets.getItem("Overview").getRange("B12").format.numberFormat = "0.00";
executionBook.worksheets.getItem("Overview").getRange("B13").format.numberFormat = "0.00%";
executionBook.worksheets.getItem("Overview").getRange("B14").format.numberFormat = '"$"#,##0.00';
executionBook.worksheets.getItem("Overview").getRange("B15").format.numberFormat = "#,##0";
executionBook.worksheets.getItem("Overview").getRange("B16").format.numberFormat = '"$"#,##0.00';
const orderSheet = await addCsvSheet(executionBook, "Rebalance Orders", "历史调仓草稿", "所有 approved_trade_shares 均为 0；表格仅演示从目标组合到交易清单的转换。", "latest_selection/rebalance_orders.csv");
orderSheet.getRange("G6:G100").format.numberFormat = '"$"#,##0.00';
orderSheet.getRange("H6:I100").format.numberFormat = '"$"#,##0.00';
const annualSheet = await addCsvSheet(executionBook, "Annual Returns", "年度收益对照", "策略、SPY 与当前股票池等权基准。", "vectorbt/annual_returns.csv");
annualSheet.getRange("B6:D100").format.numberFormat = "0.00%";
await addCsvSheet(executionBook, "Benchmark", "每日基准与策略净值", "完整日度序列，用于复核收益与回撤。", "vectorbt/benchmark_comparison.csv");
await addCsvSheet(executionBook, "Engine Orders", "vectorbt 引擎订单", "vectorbt 1.0.0 原始订单记录的可读映射。", "vectorbt/engine_orders.csv");
await addCsvSheet(executionBook, "Portfolio Daily", "组合日度账本", "现金、分红应收、权益及收益。", "vectorbt/portfolio_daily.csv");
const executionChecks = await addCsvSheet(executionBook, "Checks", "执行与发布检查", "工程核算通过不代表正式回测或可下单。", "latest_selection/checks.csv");
executionChecks.getRange("B6:B100").conditionalFormats.add("containsText", { text: "BLOCKED", format: { fill: red, font: { bold: true, color: "#9C0006" } } });
executionChecks.getRange("B6:B100").conditionalFormats.add("containsText", { text: "PASS", format: { fill: green, font: { bold: true, color: "#006100" } } });
const executionQa = await verifyAndExport(executionBook, "execution_review.xlsx");

await fs.writeFile(path.join(outputDir, "workbook_qa.json"), JSON.stringify({ signal: signalQa, execution: executionQa }, null, 2) + "\n");
