#!/usr/bin/env python3
"""
deal_team.py -- who the memo header may name as the deal team.

The problem
===========
The header names the deal team on the first line a partner reads, and the deal
team is the borrower's own people. Every person named in a deal folder is
the BORROWER's -- the credit agreement's signature pages, the reporting
package's certificate, the underwriting memo's management section. One setup
read a borrower's CFO out of those documents and printed it under "Deal team".

The strategy
============
Infer, then confirm. Existing deal-context files provide candidate team names,
but no inferred name is trusted until the analyst confirms it.

So a candidate name is scored, not trusted:

  ours          the same person is on another credit deal (surname + first initial,
                because files may write "Pat Example" and "P. Example" for one
                person, and "Sam Sample" and "S. Sample" for another)
  near          the surname is one small edit from a name on the book -- a
                likely misspelling, and a question for the analyst
  officer       the string carries an officer title, so it is the borrower's
  unknown       nobody on the book, no title -- possible new colleague, possible
                borrower contact; the analyst decides

Nothing is written from this. The skill puts the proposal to the analyst with
the evidence beside each name, the analyst confirms or edits, and the confirmed
list is stamped `static.deal_team_provenance` "analyst-confirmed <date>". An
unstamped list prints no deal-team line (memo_payload.settled_deal_team).

CLI
===
    python deal_team.py roster [--store <folder>] [--exclude <borrower folder>]
    python deal_team.py propose --name "P. Example" --name "Borrower CFO" \
        [--store <folder>] [--exclude <borrower folder>]

Both print JSON to stdout. `--store` defaults to the connected `store` folder.

Dependencies: Python stdlib + monitor_lib (../lib).
"""

import argparse
import difflib
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lib"))
import monitor_lib as ml                                  # noqa: E402

# A surname this close to one on the book is treated as a possible misspelling
# and goes to the analyst as a question.
NEAR_SURNAME = 0.86


def store_folder(explicit=None):
    """The folder holding the borrowers, or None when it is not connected."""
    if explicit:
        return explicit if os.path.isdir(explicit) else None
    return ml.store_root("store")


def deal_contexts(folder):
    """Every borrower's deal-context under this folder, one level down."""
    return sorted(glob.glob(os.path.join(folder, "*", "_model",
                                         "deal-context.json")))


def read_roster(folder, exclude=None):
    """Who the book staffs credit deals with, read off the book itself.

    Returns the people (each with the spellings and the deals they appear on) and
    which files were read, so the skill can say what the proposal rests on.
    """
    skip = ml.norm_label(exclude)
    people, read, unreadable = {}, [], []
    for path in deal_contexts(folder):
        deal = os.path.basename(os.path.dirname(os.path.dirname(path)))
        if skip and ml.norm_label(deal) == skip:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                dc = json.load(fh)
        except (OSError, ValueError) as exc:
            unreadable.append({"deal": deal, "why": str(exc)[:120]})
            continue
        read.append(deal)
        team = ((dc.get("static") or {}).get("deal_team") or [])
        if not isinstance(team, list):
            team = str(team).split(",")
        for name in team:
            name = str(name).strip()
            if not name or ml.names_an_officer(name):
                continue
            key = ml.person_key(name)
            if not key[0]:
                continue
            who = people.setdefault("|".join(key),
                                    {"spellings": [], "deals": []})
            if name not in who["spellings"]:
                who["spellings"].append(name)
            if deal not in who["deals"]:
                who["deals"].append(deal)
    for who in people.values():
        # The fullest spelling is the one to show: "Pat Example" over
        # "P. Example", because the header is read by a person.
        who["name"] = max(who["spellings"], key=len)
        who["deals_named_on"] = len(who["deals"])
    ordered = sorted(people.items(),
                     key=lambda kv: (-kv[1]["deals_named_on"], kv[1]["name"]))
    return {"folder": folder, "deals_read": read, "unreadable": unreadable,
            "people": [dict(v, key=k) for k, v in ordered]}


def score(name, roster):
    """One candidate name against the book: the verdict and its evidence."""
    name = str(name).strip()
    if ml.names_an_officer(name):
        return {"name": name, "verdict": "officer",
                "evidence": "the name carries an officer title, so it is the "
                            "borrower's own officer, not one of ours."}
    key = ml.person_key(name)
    for who in roster["people"]:
        if who["key"] == "|".join(key):
            deals = ", ".join(who["deals"][:4])
            # The fullest spelling anyone writes, the book's or this one's: the
            # header is read by a person, and "Pat Example" beats "P. Example".
            fullest = max((who["name"], name), key=len)
            return {"name": fullest, "as_given": name, "verdict": "ours",
                    "deals_named_on": who["deals_named_on"],
                    "evidence": f'named on {who["deals_named_on"]} other Credit Monitoring '
                                f'deal(s): {deals}.'}
    for who in roster["people"]:
        surname = who["key"].split("|")[0]
        if key[0] and difflib.SequenceMatcher(None, key[0], surname).ratio() \
                >= NEAR_SURNAME:
            return {"name": name, "verdict": "near", "closest": who["name"],
                    "evidence": f'no one on the book spells it this way; '
                                f'{who["name"]} is one small edit away, so this '
                                f'may be that name misspelled.'}
    return {"name": name, "verdict": "unknown",
            "evidence": "nobody on the book by this name, and no officer title "
                        "-- a new colleague, or a name out of the borrower's own "
                        "documents."}


def propose(candidates, folder, exclude=None):
    """The proposal to put to the analyst: each candidate scored, plus the people
    the book staffs most often, so adding a missing colleague is one pick."""
    roster = read_roster(folder, exclude)
    scored = [score(n, roster) for n in candidates if str(n).strip()]
    named = {ml.person_key(s.get("name")) for s in scored}
    return {
        "store": folder,
        "deals_read": roster["deals_read"],
        "unreadable": roster["unreadable"],
        "candidates": scored,
        "propose": [s["name"] for s in scored if s["verdict"] == "ours"],
        "ask_about": [s for s in scored if s["verdict"] != "ours"],
        "others_on_the_book": [
            {"name": w["name"], "deals_named_on": w["deals_named_on"]}
            for w in roster["people"] if ml.person_key(w["name"]) not in named],
    }


def _cli(argv=None):
    ap = argparse.ArgumentParser(description="Deal-team names for the memo header")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("roster", "propose"):
        p = sub.add_parser(name)
        p.add_argument("--store", help="the folder holding the borrowers")
        p.add_argument("--exclude", help="this borrower's own folder name")
        if name == "propose":
            p.add_argument("--name", action="append", default=[],
                           help="a candidate name; repeat the flag")
    a = ap.parse_args(argv)

    folder = store_folder(a.store)
    if not folder:
        print(json.dumps({"error": "the monitoring folder is not connected, so "
                                   "no deal team can be proposed from the book"}))
        return 2
    out = read_roster(folder, a.exclude) if a.cmd == "roster" \
        else propose(a.name, folder, a.exclude)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
