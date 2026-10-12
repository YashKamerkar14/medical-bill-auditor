"""Audit one synthetic bill from the command line.

    uv run python -m bill_auditor.audit examples/hard-m09-bill.pdf --eob examples/hard-m09-eob.pdf
    uv run python -m bill_auditor.audit examples/hard-t02-bill.json --full
    uv run python -m bill_auditor.audit examples/hard-t02-bill.json --letter letter.txt --i-have-reviewed

Prints the report. `--letter` drafts a dispute letter from every verified,
high-confidence error; it requires `--i-have-reviewed`, your confirmation that
you checked each item against the bill and EOB (the Streamlit app lets you pick
items one by one).
"""

import argparse
import sys
from pathlib import Path

from bill_auditor.letter.letter import LetterNotAllowed, build_letter, eligible_flags
from bill_auditor.parsing.schemas import Bill
from bill_auditor.report.report import render_markdown
from bill_auditor.ui.core import InputError, load_document, make_auditor


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bill", type=Path, help="the bill, .json or .pdf (synthetic only)")
    parser.add_argument("--eob", type=Path, help="the insurer's EOB, .json or .pdf")
    parser.add_argument("--full", action="store_true", help="use the full system (free-tier model; needs GOOGLE_API_KEY)")
    parser.add_argument("--letter", type=Path, help="write a draft dispute letter to this file")
    parser.add_argument("--i-have-reviewed", action="store_true",
                        help="confirm you checked every item against your bill and EOB (needed for --letter)")
    args = parser.parse_args(argv)
    try:
        bill = load_document(args.bill.name, args.bill.read_bytes(), "bill")
        eob = load_document(args.eob.name, args.eob.read_bytes(), "eob") if args.eob else None
        auditor = make_auditor("full" if args.full else "rules")
    except (InputError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    run = auditor.run(bill, eob)
    print(render_markdown(run.report))
    print(f"\n({auditor.model_calls})")
    if args.letter:
        if run.report.status != "completed":
            print("error: no letter: the bill was not audited", file=sys.stderr)
            return 1
        try:
            flags = eligible_flags(run.report)
            letter = build_letter(run.report, Bill.model_validate(bill), [f.flag_id for f in flags],
                                  reviewed=args.i_have_reviewed)
        except LetterNotAllowed as error:
            print(f"error: no letter: {error}", file=sys.stderr)
            return 1
        args.letter.write_text(letter.text, encoding="utf-8")
        print(f"Draft letter with {len(letter.flag_ids)} item(s) written to {args.letter}. Review and edit it before sending.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
