#!/usr/bin/env python3

"""
DDLS (DaeDaLuS) CLI skeleton.
"""

import argparse
import sys
import json

from config import *
from core import *

store = Store(Fetcher())

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ddls", description="Demo Package Manager CLI")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # -------- fetcher --------
    fetcher_parser = subparsers.add_parser("fetcher", help="Fetcher operations")
    fetcher_group = fetcher_parser.add_mutually_exclusive_group(required=True)

    fetcher_group.add_argument("-i", metavar="PACKAGE", help="Get package info")
    fetcher_group.add_argument("-d", nargs="+", metavar=("ID", "VERSION"), help="Download by hash or name [version]")
    fetcher_group.add_argument("-dl", metavar="NAME", help="Download latest version")

    # -------- store --------
    store_parser = subparsers.add_parser("store", help="Store operations")
    store_sub = store_parser.add_subparsers(dest="store_cmd", required=True)

    add_parser = store_sub.add_parser("add", help="Add package to store")
    add_parser.add_argument("path")

    remove_parser = store_sub.add_parser("remove", help="Remove package from store")
    remove_parser.add_argument("path")

    store_sub.add_parser("rollback", help="Rollback store")

    return parser

def manage_generation(command: str, pkg_path: str):
    pass


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "fetcher":
        if args.i: #ddls fetcher -i <package>
            resp = store.fetcher.get(ENDPOINTS["-i"], {"name": args.i})
            print(json.dumps(resp, indent=4, sort_keys=True) if isinstance(resp, list) \
                   else resp)
            
        elif args.d:
            arg = args.d
            query : Dict = store.fetcher.get(ENDPOINTS["-ih"], {"hash": arg[0]}) if len(arg) == 1 \
                else store.fetcher.get(ENDPOINTS["-ih"],{"name" : arg[0], "version" : arg[1]})

            print(f"statuse: {store.update(query)}")

    elif args.command == "store":
        manage_generation(args.store_cmd, args.path)

    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
