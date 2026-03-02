from email.message import Message
import json
import urllib.request
from urllib.parse import urljoin
import urllib.error
import os
import logging

from config import *

logger = logging.getLogger("Fetcher")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Fetcher:
    def __init__(self, headers=None):
        self.headers = headers if headers else {}
    
    def get(self, endpoint : Enum, params=None) -> Dict:
        """
        Sends a GET request.
        :param endpoint: The API path
        :param params: Dictionary of query parameters
        :return: JSON response or raw content
        """
        url = self._get_full_url(endpoint)
        
        # Manual parameter encoding 
        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"

        # Create Request object with headers
        req = urllib.request.Request(url, headers=self.headers)
        
        response_text = ""
        try:
            with urllib.request.urlopen(req) as response:
                response_text = response.read().decode('utf-8')
                return json.loads(response_text)
        
        except urllib.error.HTTPError as err:
            raise RuntimeError(f"Distributer Error: {err.code} {err.reason}")
        
        except json.JSONDecodeError:
            raise RuntimeError(response_text) # raise text if response isn't JSON
        
        except urllib.error.URLError as e:
            raise RuntimeError("Failed to contact distributor") from e
    
    def download_file(self, save_path, store_path, endpoint: Enum = ENDPOINTS.DOWNLOAD) -> str | None:
        """
        Downloads a file/stream and saves it to save_path.
        :param params:
        :param endpoint: The API path
        :param save_path: Where to save the file
        """
        url = self._get_full_url(endpoint)
        
        query_string = urllib.parse.urlencode({"Store_path" : store_path})
        url = f"{url}?{query_string}"
            
        req = urllib.request.Request(url, headers=self.headers)

        try:
            if not os.path.exists(save_path):
                raise FileNotFoundError(f"Save path {save_path} does not exist.")
            
            with urllib.request.urlopen(req, timeout=10) as response:
                
                cd = response.headers.get('Content-Disposition', '')
                filename = self.get_filename(cd)

                if filename is None:
                    raise Exception("Filename not found in response headers.")

                with open(os.path.join(save_path, filename), 'wb') as f:
                    while True:
                        chunk = response.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)

            return filename

        # HTTPError handling
        except urllib.error.HTTPError as err:
            logger.error(f"HTTP Error: {err.code} {err.reason}")
        except Exception as e:
            logger.error(f"An error occurred: {e}")

        return None
    
    @staticmethod 
    def get_filename(cd_header):
        msg = Message()
        msg['Content-Disposition'] = cd_header
        filename = msg.get_filename()

        if not filename:
            return None
        filename = os.path.basename(filename)

        # SECURITY: This prevents hidden control characters or shell injection characters
        keep_chars = ('.', '_', '-')
        filename = "".join(c for c in filename if c.isalnum() or c in keep_chars).strip()

        return filename 
    
    @staticmethod
    def _get_full_url(endpoint : Enum):
        """Helper to join the base URL with the endpoint safely."""
        return urljoin(STORE_NODE, endpoint.value)