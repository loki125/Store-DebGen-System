import json
import urllib.request
import urllib.parse
import urllib.error
import logging
from email.message import Message
from pathlib import Path
from typing import Dict, Any, Optional

from config import STORE_NODE
from .utils import APIEndpoints, APIParams

class Fetcher:
    def __init__(self, headers: Optional[Dict[str, str]] = None):
        """!
        @brief Initializes the Fetcher instance with optional HTTP headers and a logger.

        @param headers Optional dictionary of HTTP headers to include in requests.
        """
        self.headers = headers or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _make_request(self, endpoint: str, params: Optional[Dict[str, str]] = None):
        """!
        @brief Builds the URL and executes an HTTP request, returning the open response.

        @param endpoint The target API endpoint path.
        @param params Optional dictionary of query parameters.
        @return The open HTTP response object.
        """
        url = urllib.parse.urljoin(STORE_NODE, endpoint)
        
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        req = urllib.request.Request(url, headers=self.headers)
        return urllib.request.urlopen(req, timeout=10)

    def _get_json(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """!
        @brief Executes an HTTP request and parses the response body as JSON.

        @param endpoint The target API endpoint path.
        @param params Optional dictionary of query parameters.
        @return A dictionary containing the parsed JSON response data.
        """
        try:
            with self._make_request(endpoint, params) as response:
                response_text = response.read().decode('utf-8')
                return json.loads(response_text)
                
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"Server returned HTTP {err.code}: {err.reason}") from err
        except json.JSONDecodeError:
            raise RuntimeError("Failed to parse JSON response from server.")
        except urllib.error.URLError as err:
            raise RuntimeError("Failed to connect to the store node.") from err
        
    def get_recipe_pkg(self, store_path: Path | str) -> Dict[str, Any]:
        """!
        @brief Retrieves the recipe package details for a given store path.

        @param store_path The path or string identifier of the store item.
        @return A dictionary containing the package recipe data.
        """
        return self._get_json(
            APIEndpoints.RECIPE_PKG,
            {APIParams.STORE_PATH: str(store_path)}
        )


    def get_packages_by_name(self, package_name: str) -> Dict[str, Any]:
        """!
        @brief Retrieves packages matching a specific name from the store.

        @param package_name The name of the package to search for.
        @return A dictionary containing the matching package details.
        """
        return self._get_json(
            APIEndpoints.PKGS_BY_NAME,
            {APIParams.PACKAGE: package_name}
        )


    def get_packages_by_name_version(
        self,
        package_name: str,
        version: str
    ) -> Dict[str, Any]:
        """!
        @brief Retrieves packages matching a specific name and version.

        @param package_name The name of the package.
        @param version The version string of the package.
        @return A dictionary containing the matching package details.
        """
        return self._get_json(
            APIEndpoints.PKGS_BY_NAME_VERSION,
            {
                APIParams.PACKAGE: package_name,
                APIParams.VERSION: version
            }
        )


    def get_package_by_hash(self, sha256_hash: str) -> Dict[str, Any]:
        """!
        @brief Retrieves a package matching the specified SHA-256 hash.

        @param sha256_hash The SHA-256 hash string of the package.
        @return A dictionary containing the package details.
        """
        return self._get_json(
            APIEndpoints.PKG_BY_HASH,
            {APIParams.SHA256: sha256_hash}
        )


    def download_file(
        self,
        save_dir: Path,
        relative_store_path: Path | str
    ) -> Optional[Path]:
        """!
        @brief Downloads a file from the store node and saves it to the specified directory.

        @param save_dir The directory path where the downloaded file will be saved.
        @param relative_store_path The relative path of the file on the store node.
        @return The path to the downloaded file, or None if the download fails.
        """
        try:
            if not save_dir.exists() or not save_dir.is_dir():
                raise FileNotFoundError(
                    f"Target directory does not exist: {save_dir}"
                )

            with self._make_request(
                APIEndpoints.DOWNLOAD_PKG,
                {APIParams.STORE_PATH: str(relative_store_path)}
            ) as response:

                cd_header = response.headers.get(
                    "Content-Disposition",
                    ""
                )

                filename = (
                    self.get_filename(cd_header)
                    or "pkg.zip"
                )

                zip_path = save_dir / filename

                with open(zip_path, "wb") as f:
                    while chunk := response.read(8192):
                        f.write(chunk)

                return zip_path

        except urllib.error.HTTPError as err:
            self.logger.error(
                f"Download failed - HTTP {err.code}: {err.reason}"
            )

        except Exception as e:
            self.logger.error(f"Download error: {e}")

        return None

    @staticmethod 
    def get_filename(cd_header: str) -> Optional[str]:
        """!
        @brief Safely extracts and cleans the filename from a Content-Disposition header.

        @param cd_header The raw Content-Disposition header string.
        @return The sanitized filename string, or None if extraction fails.
        """
        if not cd_header:
            return None
            
        msg = Message()
        msg['Content-Disposition'] = cd_header
        filename = msg.get_filename()

        if not filename:
            return None

        filename = Path(filename).name
        
        # SECURITY: Strip hidden control characters or shell injection attempts
        keep_chars = ('.', '_', '-')
        return "".join(c for c in filename if c.isalnum() or c in keep_chars).strip()