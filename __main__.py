"""Command line: python -m verity {serve|share|init|run|demo}"""

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from .agent import run
from .excel_io import write_template

_HERE = Path(__file__).parent


def _demo_input(path):
    """An input sheet pointing at the fictional saved pages in verity/samples."""
    from openpyxl import Workbook
    from .schema import INPUT_COLUMNS
    samples = _HERE / "samples"
    rows = [
        ["1", "AVERY", "", "SAMPLE", "", "Registered Nurse License", "Texas Board of Nursing", "900001", "TX",
         "3/31/2028", "Active", "8/14/2012", "No", "Multistate", str(samples / "sample_clear.html"), ""],
        ["2", "JORDAN", "", "EXAMPLE", "", "Registered Nurse License", "Texas Board of Nursing", "900002", "TX",
         "5/31/2027", "Active", "2/3/2016", "No", "Multistate", str(samples / "sample_discrepancy.html"), ""],
        ["3", "RILEY", "", "TESTCASE", "", "LVN", "Texas Board of Nursing", "900003", "TX",
         "10/31/2025", "Delinquent", "7/9/2009", "No", "Single State", str(samples / "sample_alert.html"),
         "Expired licenses must be escalated"],
        ["4", "MORGAN", "", "PLACEHOLDER", "", "Registered Nurse License", "Texas Board of Nursing", "900004", "TX",
         "1/31/2027", "Active", "", "No", "", "https://example.org/license-search", ""],
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "Input"
    ws.append([h for _, h, _ in INPUT_COLUMNS])
    for row in rows:
        ws.append(row)
    wb.save(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="verity", description="Verity - professional license verification agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="create a blank input workbook")
    p_init.add_argument("path", nargs="?", default="verity_input.xlsx")
    for name, text in (("run", "verify every row of an input workbook"), ("demo", "run on fictional sample pages")):
        p = sub.add_parser(name, help=text)
        if name == "run":
            p.add_argument("input")
        p.add_argument("-o", "--out", help="output folder (default: verity/output/<timestamp>)")
        p.add_argument("--timeout", type=int, default=180, help="seconds to wait for the security check")
        p.add_argument("--no-open", action="store_true", help="do not open the report when finished")
    p_serve = sub.add_parser("serve", help="open the harness UI to run and monitor the agent")
    p_serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    p_serve.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this machine only)")
    p_serve.add_argument("--access-code", help="require this code to sign in; 'auto' generates one")
    p_serve.add_argument("--no-open", action="store_true", help="do not open the browser")
    p_share = sub.add_parser("share", help="serve the harness behind an access code with a public link")
    p_share.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    args = parser.parse_args(argv)

    if args.cmd == "share":
        from .server import share
        share(args.port)
        return

    if args.cmd == "serve":
        from .server import serve
        serve(args.port, not args.no_open, args.host, args.access_code)
        return

    if args.cmd == "init":
        if Path(args.path).exists():
            sys.exit(f"{args.path} already exists; choose another name.")
        write_template(args.path)
        print(f"Template written to {args.path}")
        return
    if args.cmd == "demo":
        out = Path(args.out) if args.out else _HERE / "output" / "demo"
        out.mkdir(parents=True, exist_ok=True)
        args.input = out / "demo_input.xlsx"
        _demo_input(args.input)
        args.out = out
    _, report, _ = run(args.input, args.out, args.timeout)
    if not args.no_open:
        webbrowser.open(report.resolve().as_uri())


if __name__ == "__main__":
    main()
