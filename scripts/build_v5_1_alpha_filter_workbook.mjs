import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const [runDirArg, outputArg, previewDirArg] = process.argv.slice(2);
if (!runDirArg || !outputArg) {
  throw new Error("Usage: build_v5_1_alpha_filter_workbook.mjs RUN_DIR OUTPUT_XLSX [PREVIEW_DIR]");
}

const runDir = path.resolve(runDirArg);
const outputPath = path.resolve(outputArg);
const previewDir = previewDirArg ? path.resolve(previewDirArg) : null;

async function readJson(name) {
  return JSON.parse(await fs.readFile(path.join(runDir, name), "utf8"));
}

async function readCsvValues(name) {
  const text = await fs.readFile(path.join(runDir, name), "utf8");
  const imported = await Workbook.fromCSV(text, { sheetName: "Imported" });
  return imported.worksheets.getItem("Imported").getUsedRange().values;
}

const numericHeaders = new Set([
  "factor_version", "valid_signal_sessions", "median_cross_section_coverage",
  "mean_rank_ic_5d", "icir_5d", "positive_year_ratio", "q1_mean_5d",
  "q5_mean_5d", "q5_minus_q1_5d", "weekly_top10_turnover", "total_return",
  "sharpe", "max_drawdown", "modeled_fees",
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

const status = await readJson("run_status.json");
const proposal = await readJson("proposed_active_pool.json");
const config = await readJson("filter_config.json");
const manifest = await readJson("manifest.json");
const diagnostics = typedMatrix(await readCsvValues("alpha_diagnostics.csv"));
const decisions = typedMatrix(await readCsvValues("filter_decisions.csv"));
const comparison = typedMatrix(await readCsvValues("composite_comparison.csv"));
const blocked = typedMatrix(await readCsvValues("blocked_items.csv"));

const workbook = Workbook.create();
const summarySheet = workbook.worksheets.add("Summary");
const diagnosticsSheet = workbook.worksheets.add("Alpha Diagnostics");
const selectedSheet = workbook.worksheets.add("Selected Pool");
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
  red: "#FCE8E6",
  white: "#FFFFFF",
};
const fontName = "Arial";

function applyTitle(sheet, title, context) {
  sheet.showGridLines = false;
  sheet.getRange("A2:H2").merge();
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = { name: fontName, size: 14, bold: true, color: colors.text };
  sheet.getRange("A3:H3").merge();
  sheet.getRange("A3").values = [[context]];
  sheet.getRange("A3").format.font = { name: fontName, size: 10, italic: true, color: "#667085" };
  sheet.getRange("A2:H2").format.borders = { bottom: { style: "thin", color: colors.blue } };
}

function writeTable(sheet, startRow, matrix, name) {
  const safe = matrix.length ? matrix : [["status"], ["NO_ROWS"]];
  const rowCount = safe.length;
  const colCount = Math.max(...safe.map((row) => row.length));
  const padded = safe.map((row) => [...row, ...Array(colCount - row.length).fill(null)]);
  const range = sheet.getRangeByIndexes(startRow, 0, rowCount, colCount);
  range.values = padded;
  range.format.font = { name: fontName, size: 10, color: colors.text };
  range.format.verticalAlignment = "center";
  const header = sheet.getRangeByIndexes(startRow, 0, 1, colCount);
  header.format = {
    fill: colors.navy,
    font: { name: fontName, size: 10, bold: true, color: colors.white },
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
    const longest = Math.max(...padded.map((row) => String(row[columnIndex] ?? "").length));
    const width = Math.max(10, Math.min(38, longest + 2));
    sheet.getRangeByIndexes(startRow, columnIndex, rowCount, 1).format.columnWidth = width;
  }
  sheet.freezePanes.freezeRows(startRow + 1);
  return { name, rowCount, colCount };
}

applyTitle(
  summarySheet,
  "V5.1 Alpha Filter Review",
  "On-demand factor diagnostics and human Active Pool review",
);
const summaryRows = [
  ["Item", "Value"],
  ["Run status", status.status ?? proposal.status],
  ["Filter run ID", status.filter_run_id ?? proposal.filter_run_id],
  ["Catalog factor versions", status.catalog_factor_version_count ?? null],
  ["Selected factors", status.selected_factor_count ?? proposal.factor_count],
  ["Provider", proposal.provider],
  ["Sample", `${proposal.sample_start ?? ""} to ${proposal.sample_end ?? ""}`],
  ["Composite status", proposal.blockers?.length ? proposal.blockers.join(", ") : "READY_FOR_HUMAN_REVIEW"],
  ["Evidence", proposal.selection_evidence],
];
writeTable(summarySheet, 4, summaryRows, "Summary");
summarySheet.getRange("A5:A13").format.font = { name: fontName, size: 10, bold: true, color: colors.text };
summarySheet.getRange("B5:B13").format.fill = colors.lightBlue;

applyTitle(
  diagnosticsSheet,
  "Alpha Diagnostics",
  "Weekly signal dates; forward return uses t+1 open to t+6 open",
);
writeTable(diagnosticsSheet, 4, diagnostics, "Alpha Diagnostics");

applyTitle(
  selectedSheet,
  "Selected Pool",
  "Review proposal factors. Decision and comment cells are editable review inputs.",
);
const selectedHeaders = ["factor_id", "factor_version", "decision", "comment"];
const selectedRows = proposal.selected_factors?.map((row) => [
  row.factor_id,
  row.factor_version,
  "",
  "",
]) ?? [];
const selectedMatrix = [selectedHeaders, ...selectedRows];
writeTable(selectedSheet, 4, selectedMatrix, "Selected Pool");
if (selectedRows.length) {
  const decisionRange = selectedSheet.getRangeByIndexes(5, 2, selectedRows.length, 1);
  decisionRange.format.fill = colors.amber;
  decisionRange.dataValidation = { rule: { type: "list", values: ["APPROVE", "REJECT"] } };
  selectedSheet.getRangeByIndexes(5, 3, selectedRows.length, 1).format.fill = colors.amber;
}

applyTitle(
  comparisonSheet,
  "Composite Comparison",
  "Portfolio metrics are diagnostic only and never determine factor admission",
);
writeTable(comparisonSheet, 4, comparison, "Composite Comparison");
comparisonSheet.getRange("C5:C7").format.columnWidth = 52;
comparisonSheet.getRange("D5:D7").format.columnWidth = 44;

applyTitle(
  auditSheet,
  "Audit",
  "Frozen timing, evidence and execution boundaries",
);
const auditRows = [
  ["Control", "Recorded value"],
  ["selection_evidence", String(manifest.selection_evidence ?? proposal.selection_evidence)],
  ["historical_pit_verified", `historical_pit_verified=${String(manifest.historical_pit_verified)}`],
  ["survivorship_bias", `survivorship_bias=${String(manifest.survivorship_bias)}`],
  ["primary_frequency", String(config.primary_frequency)],
  ["forward_label", "t close -> t+1 open -> t+6 open"],
  ["live_orders_submitted", `live_orders_submitted=${String(manifest.live_orders_submitted)}`],
  ["filter_run_id", String(manifest.filter_run_id ?? proposal.filter_run_id)],
  ["blocked_item_count", Math.max(0, blocked.length - 1)],
  ["decision_row_count", Math.max(0, decisions.length - 1)],
];
writeTable(auditSheet, 4, auditRows, "Audit");
auditSheet.getRange("A5:A14").format.font = { name: fontName, size: 10, bold: true, color: colors.text };

workbook.recalculate();
const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const sheetName of [
    "Summary", "Alpha Diagnostics", "Selected Pool", "Composite Comparison", "Audit",
  ]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
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
      sheet_names: [
        "Summary", "Alpha Diagnostics", "Selected Pool", "Composite Comparison", "Audit",
      ],
      formula_error_scan: errorScan.ndjson,
      preview_count: previewDir ? 5 : 0,
    },
    null,
    2,
  ) + "\n",
);
