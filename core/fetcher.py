import requests
import re
import json
from urllib.parse import urljoin

from config import *

logger = logging.getLogger("Fetcher")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class Fetcher:
    def __init__(self, store_node_url=STORE_NODE, headers=None):
        self.base_url = store_node_url
        self.session = requests.Session()
        
        if headers:
            self.session.headers.update(headers)

    def _get_full_url(self, endpoint):
        """Helper to join the base URL with the endpoint safely."""
        return urljoin(self.base_url, endpoint)
    
    def get(self, endpoint, params=None) -> Dict :
        """
        Sends a GET request.
        :param endpoint: The API path
        :param params: Dictionary of query parameters
        :return: JSON response or raw content
        """
        url = self._get_full_url(endpoint)
        response = ""
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status() # Raises error for 4xx or 5xx status codes

            return response.json()
        
        except requests.exceptions.HTTPError as err:
            raise RuntimeError(f"Distributer Error: {err}")
        
        except json.JSONDecodeError:
            raise RuntimeError(response.text) # raise text if response isn't JSON
        
        except requests.RequestException as e:
            raise RuntimeError("Failed to contact distributor") from e      
    def download_file(self, save_path, endpoint="download_pkg", params=None) -> str | None:
        """
        Downloads a file/stream and saves it to save_path.
        :param params:
        :param endpoint: The API path
        :param save_path: Where to save the file
        """
        url = self._get_full_url(endpoint)
        
        # stream=True ensures we don't download the whole file into RAM at once
        try:
            if not os.path.exists(save_path):
                raise FileNotFoundError(f"Save path {save_path} does not exist.")
            
            with self.session.get(url, params={"store_path" : params}, stream=True, timeout=10) as response:
                response.raise_for_status()
                
                cd = response.headers.get('Content-Disposition', '')
                filename_match = re.search(r'filename="(.+)"', cd)
                filename = filename_match.group(1) if filename_match else None
                if filename is None:
                    raise Exception("Filename not found in response headers.")

                with open(os.path.join(save_path, filename), 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            return filename
        except requests.exceptions.HTTPError as err:
            logger.error(f"HTTP Error: {err}")
        except Exception as e:
            logger.error(f"An error occurred: {e}")

        return None