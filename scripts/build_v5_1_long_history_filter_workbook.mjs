import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [runDirArg, outputArg, previewDirArg] = process.argv.slice(2);
if (!runDirArg || !outputArg) {
  throw new Error(
    "Usage: build_v5_1_long_history_filter_workbook.mjs RUN_DIR OUTPUT_XLSX [PREVIEW_DIR]",
  );
}

const runDir = path.resolve(runDirArg);
const filterDir = path.join(runDir, "filter");
const outputPath = path.resolve(outputArg);
const previewDir = previewDirArg ? path.resolve(previewDirArg) : null;

async function readJson(relativePath) {
  return JSON.parse(await fs.readFile(path.join(runDir, relativePath), "utf8"));
}

async function readCsvValues(relativePath) {
  const text = await fs.readFile(path.join(runDir, relativePath), "utf8");
  const imported = await Workbook.fromCSV(text, { sheetName: "Imported" });
  return imported.worksheets.getItem("Imported").getUsedRange().values;
}

const numericHeaders = new Set([
  "stage_order", "remaining_factor_versions", "removed_at_stage", "factor_version",
  "warmup_sessions", "direction", "valid_signal_sessions", "median_cross_section_coverage",
  "mean_rank_ic_5d", "icir_5d", "positive_year_ratio", "q1_mean_5d", "q5_mean_5d",
  "q5_minus_q1_5d", "weekly_top10_turnover", "median_abs_score_correlation",
  "mean_top10_jaccard", "total_return", "sharpe", "max_drawdown", "modeled_fees",
  "factor_count",
]);

function typedMatrix(values) {
  if (!values.length) return values;
  const headers = values[0].map(String);
  return values.map((row, rowIndex) => row.map((value, columnIndex) => {
    if (rowIndex === 0 || value === null || value === "") return value;
    if (numericHeaders.has(headers[columnIndex])) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : value;
    }
    if (value === "true") return true;
    if (value === "false") return false;
    return value;
  }));
}

const summary = await readJson("run_summary.json");
const evidence = await readJson("inputs/source_evidence.json");
const status = await readJson("filter/run_status.json");
const proposal = await readJson("filter/proposed_active_pool.json");
const config = await readJson("filter/filter_config.json");
const manifest = await readJson("filter/manifest.json");
const funnel = typedMatrix(await readCsvValues("filter/gate_funnel.csv"));
const decisions = typedMatrix(await readCsvValues("filter/filter_decisions.csv"));
const selected = typedMatrix(await readCsvValues("filter/final_selected_factors.csv"));
const redundancy = typedMatrix(await readCsvValues("filter/redundancy_deletions.csv"));
const comparison = typedMatrix(await readCsvValues("filter/composite_comparison.csv"));

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Summary");
const funnelSheet = workbook.worksheets.add("Gate Funnel");
const decisionsSheet = workbook.worksheets.add("Factor Decisions");
const selectedSheet = workbook.worksheets.add("Final Pool");
const redundancySheet = workbook.worksheets.add("Redundancy");
const comparisonSheet = workbook.worksheets.add("Composite Comparison");
const auditSheet = workbook.worksheets.add("Audit");

const colors = {
  navy: "#183B56",
  blue: "#2F75B5",
  lightBlue: "#D9EAF7",
  lightGray: "#F2F4F7",
  border: "#D0D5DD",
  text: "#172B4D",
  amber: "#FFF2CC",
  green: "#E2F0D9",
  red: "#FCE8E6",
  white: "#FFFFFF",
};
const fontName = "Arial";

function applyTitle(sheet, title, context) {
  sheet.showGridLines = false;
  sheet.getRange("A2:H2").merge();
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = {
    name: fontName, size: 14, bold: true, color: colors.text,
  };
  sheet.getRange("A3:H3").merge();
  sheet.getRange("A3").values = [[context]];
  sheet.getRange("A3").format.font = {
    name: fontName, size: 10, italic: true, color: "#667085",
  };
  sheet.getRange("A2:H2").format.borders = {
    bottom: { style: "thin", color: colors.blue },
  };
}

function writeTable(sheet, startRow, matrix) {
  const safe = matrix.length ? matrix : [["status"], ["NO_ROWS"]];
  const rowCount = safe.length;
  const colCount = Math.max(...safe.map((row) => row.length));
  const padded = safe.map((row) => [...row, ...Array(colCount - row.length).fill(null)]);
  const range = sheet.getRangeByIndexes(startRow, 0, rowCount, colCount);
  range.values = padded;
  range.format.font = { name: fontName, size: 9, color: colors.text };
  range.format.verticalAlignment = "center";
  const header = sheet.getRangeByIndexes(startRow, 0, 1, colCount);
  header.format = {
    fill: colors.navy,
    font: { name: fontName, size: 9, bold: true, color: colors.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "inside", style: "thin", color: colors.white },
  };
  if (rowCount > 1) {
    sheet.getRangeByIndexes(startRow + 1, 0, rowCount - 1, colCount).format.borders = {
      insideHorizontal: { style: "thin", color: colors.border },
      insideVertical: { style: "thin", color: colors.border },
    };
  }
  for (let columnIndex = 0; columnIndex < colCount; columnIndex += 1) {
    const longest = Math.max(...padded.slice(0, 50).map((row) => String(row[columnIndex] ?? "").length));
    const width = Math.max(9, Math.min(38, longest + 2));
    sheet.getRangeByIndexes(startRow, columnIndex, rowCount, 1).format.columnWidth = width;
  }
  sheet.freezePanes.freezeRows(startRow + 1);
  return { rowCount, colCount };
}

function formatMetricColumns(sheet, matrix, startRow = 4) {
  if (!matrix.length) return;
  const headers = matrix[0].map(String);
  const percentHeaders = new Set([
    "median_cross_section_coverage", "positive_year_ratio", "q1_mean_5d",
    "q5_mean_5d", "q5_minus_q1_5d", "weekly_top10_turnover", "total_return",
    "max_drawdown",
  ]);
  const decimalHeaders = new Set([
    "mean_rank_ic_5d", "icir_5d", "median_abs_score_correlation",
    "mean_top10_jaccard", "sharpe",
  ]);
  for (let index = 0; index < headers.length; index += 1) {
    if (percentHeaders.has(headers[index]) && matrix.length > 1) {
      sheet.getRangeByIndexes(startRow + 1, index, matrix.length - 1, 1).format.numberFormat = "0.0000%";
    }
    if (decimalHeaders.has(headers[index]) && matrix.length > 1) {
      sheet.getRangeByIndexes(startRow + 1, index, matrix.length - 1, 1).format.numberFormat = "0.000000";
    }
    if (headers[index] === "modeled_fees" && matrix.length > 1) {
      sheet.getRangeByIndexes(startRow + 1, index, matrix.length - 1, 1).format.numberFormat = "$#,##0.00";
    }
  }
}

applyTitle(
  summarySheet,
  "V5.1 Lite 长历史 Alpha Filter 工程验收",
  "Yahoo Finance 500-stock current-snapshot sample; results are DEVELOPMENT_EXPOSED and not proof of Alpha effectiveness",
);
const summaryRows = [
  ["Item", "Value", "Interpretation"],
  ["Run status", summary.status, "Ready for human review; not frozen for production"],
  ["Filter run ID", summary.filter_run_id, "Immutable run identifier"],
  ["Provider adapter", evidence.provider_adapter, "Saved Yahoo Finance batch files"],
  ["Sample", `${evidence.actual_start} to ${evidence.actual_end}`, `${evidence.sessions} daily sessions`],
  ["Universe", evidence.universe_count, evidence.membership],
  ["Catalog factor versions", summary.gate_summary.catalog_count, "Full V4 local library"],
  ["Hard Gate pass", summary.gate_summary.gate_pass_count, "Before redundancy removal"],
  ["Redundancy removals", summary.gate_summary.redundancy_reject_count, "No pool-cap removal"],
  ["Final proposed pool", summary.gate_summary.selected_count, "Within approved 8–15 range"],
  ["Timing contract", config.primary_frequency, config.forward_label],
  ["Evidence", summary.selection_evidence, "Historical PIT not verified; survivorship bias present"],
  ["Effectiveness claim", "NOT ALLOWED", "Engineering workflow acceptance only"],
  ["Live orders", summary.live_orders_submitted, "Manual review workflow; no broker submission"],
];
writeTable(summarySheet, 4, summaryRows);
summarySheet.getRange("A5:A18").format.font = { name: fontName, size: 9, bold: true, color: colors.text };
summarySheet.getRange("B5:B18").format.fill = colors.lightBlue;
summarySheet.getRange("C16:C17").format.fill = colors.amber;

applyTitle(
  funnelSheet,
  "135 因子 Gate 漏斗",
  "Frozen V5.1 Lite thresholds; no threshold was changed after observing results",
);
writeTable(funnelSheet, 4, funnel);

applyTitle(
  decisionsSheet,
  "全部因子决策",
  "One row per V4 factor version, including catalog, signal, stability, usability and redundancy decisions",
);
writeTable(decisionsSheet, 4, decisions);
formatMetricColumns(decisionsSheet, decisions);

applyTitle(
  selectedSheet,
  "最终通过因子",
  "14-factor proposed Active Pool after hard Gates and deterministic redundancy removal",
);
writeTable(selectedSheet, 4, selected);
formatMetricColumns(selectedSheet, selected);
if (selected.length > 1) {
  selectedSheet.getRangeByIndexes(5, 0, selected.length - 1, selected[0].length).format.fill = colors.green;
}

applyTitle(
  redundancySheet,
  "冗余删除原因",
  "same mechanism is a warning; deletions are driven by family, score correlation or top-decile overlap conflicts",
);
writeTable(redundancySheet, 4, redundancy);
formatMetricColumns(redundancySheet, redundancy);

applyTitle(
  comparisonSheet,
  "新 Composite 与旧 8-factor Baseline",
  "Same provider, universe, weekly signal dates, t close to t+1 open to t+6 open label, and portfolio assumptions",
);
writeTable(comparisonSheet, 4, comparison);
formatMetricColumns(comparisonSheet, comparison);
comparisonSheet.getRange("A5:A6").format.font = { name: fontName, size: 9, bold: true, color: colors.text };
comparisonSheet.getRange("A5:R5").format.fill = colors.green;
comparisonSheet.getRange("A6:R6").format.fill = colors.lightGray;

applyTitle(
  auditSheet,
  "证据边界与审计",
  "The workbook records what the run proves and what it does not prove",
);
const auditRows = [
  ["Control", "Recorded value", "Status / source"],
  ["run_purpose", evidence.run_purpose, "Engineering diagnostic"],
  ["selection_evidence", evidence.selection_evidence, "DEVELOPMENT_EXPOSED"],
  ["historical_pit_verified", evidence.historical_pit_verified, "False"],
  ["survivorship_bias", proposal.survivorship_bias, evidence.membership],
  ["independent_oos_claim_allowed", evidence.independent_oos_claim_allowed, "False"],
  ["alpha_effectiveness_claim_allowed", manifest.alpha_effectiveness_claim_allowed, "False"],
  ["source_price_adjustment", evidence.source_price_adjustment, "Yahoo adjusted OHLCV"],
  ["VWAP binding", evidence.vwap_binding, "Explicit proxy, not exchange VWAP"],
  ["median open coverage", evidence.median_open_cross_section_coverage, "Cross-sectional"],
  ["minimum open coverage", evidence.minimum_open_cross_section_coverage, "Cross-sectional"],
  ["active pool bounds", `${config.active_pool_min}–${config.active_pool_max}`, "Approved contract"],
  ["primary frequency", config.primary_frequency, "Frozen"],
  ["forward label", config.forward_label, "Frozen"],
  ["comparison status", manifest.composite_comparison_status, "Portfolio metrics never admit factors"],
  ["live_orders_submitted", manifest.live_orders_submitted, "0"],
  ["source URL", "https://finance.yahoo.com/", "Public source reference"],
  ["adapter URL", "https://github.com/ranaroussi/yfinance", "Adapter reference"],
];
writeTable(auditSheet, 4, auditRows);
auditSheet.getRange("A5:A22").format.font = { name: fontName, size: 9, bold: true, color: colors.text };
auditSheet.getRange("B7:C11").format.fill = colors.amber;
auditSheet.getRange("B21:B22").format.font = { name: fontName, size: 9, color: "#0563C1", underline: true };

workbook.recalculate();
const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
const keyInspection = await workbook.inspect({
  kind: "table",
  range: "Summary!A2:C18",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 4,
  summary: "summary verification",
});

const sheetNames = [
  "Summary", "Gate Funnel", "Factor Decisions", "Final Pool",
  "Redundancy", "Composite Comparison", "Audit",
];
if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of sheetNames) {
    const preview = await workbook.render({
      sheetName, autoCrop: "all", scale: 1, format: "png",
    });
    const filename = `${sheetName.toLowerCase().replaceAll(" ", "_")}.png`;
    await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()));
  }
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
await fs.writeFile(
  `${outputPath}.qa.json`,
  JSON.stringify(
    {
      sheet_names: sheetNames,
      formula_error_scan: errorScan.ndjson,
      summary_inspection: keyInspection.ndjson,
      preview_count: previewDir ? sheetNames.length : 0,
      run_id: status.filter_run_id,
      source_filter_dir: filterDir,
    },
    null,
    2,
  ) + "\n",
);
