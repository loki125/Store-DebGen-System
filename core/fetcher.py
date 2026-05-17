import json
import urllib.request
import urllib.parse
import urllib.error
import logging
from email.message import Message
from pathlib import Path
from typing import Dict, Any, Optional

from config import STORE_NODE
from utils import APIEndpoints, APIParams

class Fetcher:
    def __init__(self, headers: Optional[Dict[str, str]] = None):
        self.headers = headers or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _make_request(self, endpoint: str, params: Optional[Dict[str, str]] = None):
        """Core request handler that builds the URL and returns an open HTTP response."""
        url = urllib.parse.urljoin(STORE_NODE, endpoint)
        
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        req = urllib.request.Request(url, headers=self.headers)
        return urllib.request.urlopen(req, timeout=10)

    def _get_json(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Wrapper for _make_request that automatically parses and returns JSON."""
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
        return self._get_json(
            APIEndpoints.RECIPE_PKG,
            {APIParams.STORE_PATH: str(store_path)}
        )


    def get_packages_by_name(self, package_name: str) -> Dict[str, Any]:
        return self._get_json(
            APIEndpoints.PKGS_BY_NAME,
            {APIParams.PACKAGE: package_name}
        )


    def get_packages_by_name_version(
        self,
        package_name: str,
        version: str
    ) -> Dict[str, Any]:
        return self._get_json(
            APIEndpoints.PKGS_BY_NAME_VERSION,
            {
                APIParams.PACKAGE: package_name,
                APIParams.VERSION: version
            }
        )


    def get_package_by_hash(self, sha256_hash: str) -> Dict[str, Any]:
        return self._get_json(
            APIEndpoints.PKG_BY_HASH,
            {APIParams.SHA256: sha256_hash}
        )


    def download_file(
        self,
        save_dir: Path,
        relative_store_path: Path | str
    ) -> Optional[Path]:

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
        """Safely extracts the filename from a Content-Disposition header."""
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