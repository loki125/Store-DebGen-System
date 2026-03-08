#!/usr/bin/env python3

"""
DDLS (DaeDaLuS) CLI skeleton.
"""

import argparse
import sys
import subprocess
import os
import shutil
import json

from config import *
from core import *

logging.addLevelName(logging.CRITICAL, "\033[91mCRITICAL\033[0m")
logging.basicConfig(
    level=logging.DEBUG,
    format='[%(name)s:%(levelname)s] %(message)s'
)
store = Store(Fetcher())

def build_parser():
    """
    Constructs the argparse parser for the ddls tool.
    """
    parser = argparse.ArgumentParser(
        description="ddls package manager CLI",
        prog="ddls"
    )
    
    # Create sub-commands (info, update, insert)
    subparsers = parser.add_subparsers(dest='command', required=True, help='Available commands')

    # ddls info *package name*
    parser_info = subparsers.add_parser('info', help='Get package info')
    parser_info.add_argument('package', type=str, help='Name of the package')

    # ddls update *package name* *version*
    parser_update = subparsers.add_parser('update', help='Update a package')
    parser_update.add_argument('package', type=str, help='Name of the package')
    parser_update.add_argument('version', type=str, help='Version to update to')

    # ddls insert *+/-pkg-version
    parser_insert = subparsers.add_parser('insert', help='Insert or remove packages')
    parser_insert.add_argument('changes', nargs='+', help='List of changes (e.g. +pkg-1.0 -pkg-0.9)')

    #ddls reset -> nuke pkg_manager
    subparsers.add_parser('reset', help='perminently deletes all packages and generations.')

    return parser

def setup(argv):
    # Create directories if they don't exist
    for path in [BASE_DIR, STORE_ROOT, GEN_ROOT]:
        os.makedirs(path, exist_ok=True)

    if not os.path.isdir(BASE_ROOTFS):
        try:
            os.makedirs(BASE_ROOTFS, exist_ok=False)
            Bbrfs(BASE_ROOTFS).deploy()

        except Exception as e:
            logging.error(f"Error creating directory {BASE_ROOTFS}: {e}")
            store.reset_target(BASE_ROOTFS)
            sys.exit(1)

    parser = build_parser()
    return parser, parser.parse_args(argv)

def handle_insert_logic(change_args):
    """
    Processes the raw list from the insert command.
    Separates into add/remove lists and resolves hash paths.
    """
    to_add = []
    to_remove = []

    args_set = set(change_args)
    
    for root, dirs, files in os.walk(STORE_ROOT):
        for dir_name in dirs:
            package = dir_name.split("-", 1)[1]
            full_path = os.path.join(root, dir_name)

            if ('+' + package) in args_set:
                    to_add.append(full_path)
            elif ('-' + package) in args_set:
                    to_remove.append(full_path)

    return to_add, to_remove

def main(argv=None):
    parser, args = setup(argv)
    try:
        if args.command == "info":
            output = ""
            try:
                resp = store.fetcher.get(ENDPOINTS.PKG_INFO, {"Package": args.package})
                output = json.dumps(resp, indent=4, sort_keys=True)

            except Exception as e:
                output = str(e)
            
            print(output)
                
        elif args.command == "update":
            query : Dict = store.fetcher.get(ENDPOINTS.PKG_VER_INFO, {"Package": args.package, "Version" : args.version})
            print(f"statuse: {store.update(query)}")

        elif args.command == "insert":
            adds, rms = handle_insert_logic(args.changes)
            gen = Gen(store)
            curr, new = gen.create_new_gen(adds, rms)

            return int(gen.execute(curr, new))
        
        elif args.command == "reset":
            while True:
                choice = input("Reset will permanently delete all packages and generations. Are you sure? [y/n] ").strip().lower()
                
                if choice == 'y':
                    store.reset_target(BASE_DIR)
                    break
                elif choice == 'n':
                    print("Operation canceled.")
                    break
                
                print("Invalid input. Please type 'y' for yes or 'n' for no.")

        else:
            parser.print_help()
            return 1

    except Exception as e:
        logging.debug(e)
        return 1
    
    return 0



if __name__ == "__main__":
    sys.exit(main())
