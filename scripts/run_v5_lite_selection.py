"""CLI-compatible entry point for the V5 Lite personal selection workflow."""
from us_equity_alpha.cli import main
if __name__=="__main__": raise SystemExit(main(["daily-select",*__import__("sys").argv[1:]]))
