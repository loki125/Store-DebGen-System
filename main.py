#!/usr/bin/env python3

"""
DDLS (DaeDaLuS) - Containerized Package Manager
CLI Entrypoint
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Tuple, List, Dict

# Local imports
from config import *
from core import *

# Initialize global logger overrides
logging.addLevelName(logging.CRITICAL, "\033[91mCRITICAL\033[0m")

# Global instances
store = Store(Fetcher())



# CLI PARSER SETUP

def build_parser() -> argparse.ArgumentParser:
    """Constructs the argparse parser for the ddls tool."""
    parser = argparse.ArgumentParser(
        description="DDLS (DaeDaLuS) - The Containerized Package Manager",
        prog="ddls",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    # Global arguments
    parser.add_argument(
        '--debug', 
        action='store_true', 
        help='Enable debug/trace logging and verbose exception tracebacks'
    )

    # Subcommands
    subparsers = parser.add_subparsers(
        dest='command', 
        required=True, 
        title='Commands',
        metavar='<command>'
    )

    # START
    subparsers.add_parser(
        'start', 
        help='Run initial setup and create system symlinks'
    )

    # INFO
    parser_info = subparsers.add_parser('info', help='Fetch information about a specific package')
    parser_info.add_argument('package', type=str, metavar='PKG_NAME', help='Name of the target package')

    # UPDATE
    parser_update = subparsers.add_parser('update', help='Update a specific package to a new version')
    parser_update.add_argument('package', type=str, metavar='PKG_NAME', help='Name of the package')
    parser_update.add_argument('version', type=str, metavar='VERSION', help='Target version to update to')

    # SYSTEM
    parser_system = subparsers.add_parser('system', help='Update base system packages in the local store')
    parser_system.add_argument('store_path', type=str, metavar='PATH', help='Path to the system package in the store')

    # INSERT (Install/Remove)
    parser_insert = subparsers.add_parser('insert', help='Modify the environment (+install / -remove)')
    parser_insert.add_argument(
        'changes', 
        nargs='+', 
        metavar='CHANGES',
        help='List of changes to apply (e.g., +nginx-1.18.0 -curl-7.68.0)'
    )

    # RESET
    subparsers.add_parser(
        'reset', 
        help='WARNING: Permanently deletes all local packages and generations'
    )

    return parser



# ENVIRONMENT & UTILS
def setup_environment(args: argparse.Namespace) -> None:
    """Prepares directories, mounts, and configures logging dynamically."""
    
    # Configure logging based on flag
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format='[%(name)s:%(levelname)s] %(message)s',
        force=True 
    )

    # Ensure critical directories exist
    for path in [BASE_DIR, STORE_ROOT, GEN_DIR, SHARED_RUN, WRAPPER_DIR]:
        os.makedirs(path, exist_ok=True)

    # Deploy Base RootFS if missing
    if not os.path.isdir(BASE_ROOTFS):
        try:
            os.makedirs(BASE_ROOTFS, exist_ok=False)
            Bbrfs(BASE_ROOTFS).deploy()
        except Exception as e:
            raise RuntimeError(f"Failed to create Base RootFS at {BASE_ROOTFS}: {str(e)}")


def handle_insert_logic(change_args: List[str]) -> Tuple[List[Path], List[Path]]:
    """Processes raw insert strings (+pkg / -pkg) and resolves them to store paths."""
    to_add = []
    to_remove = []
    
    for pkg in change_args:
        command, striped_pkg = pkg[:INDICATOR_SIZE], pkg[INDICATOR_SIZE:]
        
        if command not in (ADD_INDICATOR, RM_INDICATOR):
            logging.warning(f"Invalid format for '{pkg}'. Prefix with '+' to add or '-' to remove.")
            continue

        store_path = store.get_package(striped_pkg)
        if store_path is None:
            logging.warning(f"Package '{striped_pkg}' not found in the local store. Skipping...")
            continue

        full_path = Path(STORE_ROOT) / store_path

        if command == ADD_INDICATOR:
            to_add.append(full_path)
        elif command == RM_INDICATOR:
            to_remove.append(full_path)
            
    return to_add, to_remove



# COMMAND HANDLERS
def cmd_start() -> int:
    """Handles the 'start' command."""
    PKG_MANAGER_LINK = "/usr/bin/ddls"
    
    if not os.path.lexists(PKG_MANAGER_LINK):
        current_script = os.path.abspath(__file__)
        try:
            os.symlink(current_script, PKG_MANAGER_LINK)
            os.chmod(current_script, 0o755)
            logging.info(f"Successfully linked: {PKG_MANAGER_LINK} -> {current_script}")
        except PermissionError:
            logging.error(f"Permission denied. Cannot create symlink at {PKG_MANAGER_LINK}. Please run 'start' with sudo.")
            return 1
    else:
        logging.info(f"Command 'ddls' is already configured at {PKG_MANAGER_LINK}")
        
    return 0

def cmd_info(args: argparse.Namespace) -> int:
    """Handles the 'info' command."""
    try:
        resp = store.fetcher.get(ENDPOINTS.PKG_INFO, {"Package": args.package})
        print(json.dumps(resp, indent=4, sort_keys=True))
        return 0
    except Exception as e:
        logging.error(f"Failed to fetch info for '{args.package}': {str(e)}")
        return 1

def cmd_update(args: argparse.Namespace) -> int:
    """Handles the 'update' command."""
    query: Dict = store.fetcher.get(ENDPOINTS.PKG_VER_INFO, {"Package": args.package, "Version": args.version})
    success = store.update(query)
    return 0 if success else 1

def cmd_system(args: argparse.Namespace) -> int:
    """Handles the 'system' command."""
    if os.path.exists(STORE_ROOT / args.store_path):
        logging.info(f"System package {args.store_path} already exists in store.")
        return 0
        
    if store.update_sys(args.store_path):
        logging.info(f"System package {args.store_path} updated successfully.")
        return 0
    else:
        logging.error("System update failed. Aborting...")
        return 1

def cmd_insert(args: argparse.Namespace) -> int:
    """Handles the 'insert' command."""
    adds, rms = handle_insert_logic(args.changes)
    
    if not adds and not rms:
        logging.warning("No valid packages found for generation changes. Canceling.")
        return 1
    
    gen = Gen(store)
    new, curr = gen.create_manifest(adds, rms)
    success = gen.execute(new, curr)
    
    return 0 if success else 1

def cmd_reset(args: argparse.Namespace) -> int:
    """Handles the 'reset' command."""
    while True:
        choice = input("WARNING: Reset will permanently delete all packages and generations. Proceed? [y/N] ").strip().lower()
        if choice == 'y':
            store.reset_target(BASE_DIR)
            if PROFILE_SCRIPT_PATH.exists():
                os.remove(PROFILE_SCRIPT_PATH)
            logging.info("Reset complete.")
            return 0
        elif choice == 'n' or choice == '':
            print("Operation canceled.")
            return 0
        print("Invalid input. Please type 'y' for yes or 'n' for no.")


def main(argv=None) -> int:
    """Main application entry point."""
    exit_code = 0
    args = None
    
    try:
        parser = build_parser()
        args = parser.parse_args(argv)

        # Map commands to their handler functions
        command_map = {
            'start': cmd_start,
            'info': cmd_info,
            'update': cmd_update,
            'system': cmd_system,
            'insert': cmd_insert,
            'reset': cmd_reset
        }

        # Setup environment (directories, logging) before routing
        setup_environment(args)

        # Route to appropriate command
        if args.command in command_map:
            exit_code = command_map[args.command](args)
        else:
            parser.print_help()
            exit_code = 1

    except KeyboardInterrupt:
        # Handle CTRL+C cleanly
        print("\nOperation interrupted by user.")
        exit_code = 130
        
    except Exception as e:
        # Global Error Catcher
        if args and args.debug:
            logging.exception("An unhandled exception occurred:")
        else:
            logging.error(f"Process failed: {str(e)}")
            
        exit_code = 1

    # ONE single return point for the entire application
    return exit_code


if __name__ == "__main__":
    sys.exit(main())