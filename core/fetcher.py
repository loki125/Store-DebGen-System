import json
import urllib.request
import urllib.parse
import urllib.error
import logging
from email.message import Message
from pathlib import Path
from typing import Dict, Any, Optional

from config import *

class Fetcher:
    """
    @brief Handles communication with the remote store node to fetch metadata and packages.
    """
    def __init__(self, headers: Optional[Dict[str, str]] = None):
        """
        @brief Initializes the fetcher with optional HTTP headers.
        @param headers A dictionary of HTTP headers to include in requests.
        """
        self.headers = headers or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _make_request(self, endpoint: str, params: Optional[Dict[str, str]] = None):
        """
        @brief Performs a low-level HTTP request to the store node.
        @param endpoint The API endpoint to hit.
        @param params Optional dictionary of query parameters.
        @return An open HTTP response object.
        """
        url = urllib.parse.urljoin(STORE_NODE, endpoint)
        
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        req = urllib.request.Request(url, headers=self.headers)
        return urllib.request.urlopen(req, timeout=FETCH_TIMEOUT)

    def _get_json(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        @brief Fetches data from an endpoint and parses the response as JSON.
        @param endpoint The API endpoint to query.
        @param params Optional dictionary of query parameters.
        @throw RuntimeError if HTTP error occurs, connection fails, or JSON is invalid.
        @return A dictionary containing the parsed JSON response.
        """
        try:
            with self._make_request(endpoint, params) as response:
                response_text = response.read().decode(ENCODING_UTF8)
                return json.loads(response_text)
                
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"Server returned HTTP {err.code}: {err.reason}") from err
        except json.JSONDecodeError:
            raise RuntimeError("Failed to parse JSON response from server.")
        except urllib.error.URLError as err:
            raise RuntimeError("Failed to connect to the store node.") from err

    def get_recipe_pkg(self, store_path: Path | str) -> Dict[str, Any]:
        """
        @brief Fetches the recipe metadata for a specific package path in the store.
        @param store_path The relative path of the package in the store.
        @return A dictionary containing the package recipe.
        """
        return self._get_json(ENDPOINT_RECIPE, {KEY_STORE_PATH: str(store_path)})

    def get_packages_by_name(self, package_name: str) -> Dict[str, Any]:
        """
        @brief Searches for all available versions of a package by its name.
        @param package_name The name of the package to search for.
        @return A dictionary containing package version information.
        """
        return self._get_json(ENDPOINT_PKGS_NAME, {KEY_PACKAGE: package_name})

    def get_packages_by_name_version(self, package_name: str, version: str) -> Dict[str, Any]:
        """
        @brief Fetches metadata for a specific package name and version.
        @param package_name The name of the package.
        @param version The specific version string.
        @return A dictionary containing the package metadata.
        """
        return self._get_json(ENDPOINT_PKGS_VER, {KEY_PACKAGE: package_name, KEY_VERSION: version})

    def get_package_by_hash(self, sha256_hash: str) -> Dict[str, Any]:
        """
        @brief Fetches package information using a SHA256 hash identifier.
        @param sha256_hash The SHA256 hash of the package.
        @return A dictionary containing the package information.
        """
        return self._get_json(ENDPOINT_PKG_HASH, {KEY_SHA256: sha256_hash})

    def download_file(self, save_dir: Path, relative_store_path: Path | str) -> Optional[Path]:
        """
        @brief Downloads a package archive from the store to a specified directory.
        @param save_dir The local directory where the file should be saved.
        @param relative_store_path The path to the file on the remote store.
        @throw FileNotFoundError if the target directory does not exist.
        @return The Path to the downloaded file, or None if the download failed.
        """
        try:
            if not save_dir.exists() or not save_dir.is_dir():
                raise FileNotFoundError(f"Target directory does not exist: {save_dir}")

            with self._make_request(ENDPOINT_DOWNLOAD, {KEY_STORE_PATH: str(relative_store_path)}) as response:
                cd_header = response.headers.get(HDR_CONTENT_DISPOSITION, '')
                filename = self.get_filename(cd_header) or DEFAULT_PKG_ZIP
                
                zip_path = save_dir / filename

                with open(zip_path, 'wb') as f:
                    while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)

                return zip_path

        except urllib.error.HTTPError as err:
            self.logger.error(f"Download failed - HTTP {err.code}: {err.reason}")
        except Exception as e:
            self.logger.error(f"Download error: {e}")

        return None

    @staticmethod 
    def get_filename(cd_header: str) -> Optional[str]:
        """
        @brief Extracts and sanitizes the filename from the Content-Disposition HTTP header.
        @param cd_header The raw Content-Disposition header string.
        @return The sanitized filename string, or None if extraction fails.
        """
        if not cd_header:
            return None
            
        msg = Message()
        msg[HDR_CONTENT_DISPOSITION] = cd_header
        filename = msg.get_filename()

        if not filename:
            return None

        filename = Path(filename).name
        
        # SECURITY: Strip hidden control characters or shell injection attempts
        return "".join(c for c in filename if c.isalnum() or c in FILENAME_KEEP_CHARS).strip()