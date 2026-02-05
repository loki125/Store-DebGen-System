import requests
from typing import Any
from urllib.parse import urljoin

from config import *

class Fetcher:
    def __init__(self, store_node_url=STORE_NODE, headers=None):
        self.base_url = store_node_url
        self.session = requests.Session()
        
        if headers:
            self.session.headers.update(headers)

    def _get_full_url(self, endpoint):
        """Helper to join the base URL with the endpoint safely."""
        return urljoin(self.base_url, endpoint)
    
    def get(self, endpoint, params=None) -> None | str | Any:
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
            print(f"HTTP Error: {err}")
        except requests.exceptions.JSONDecodeError:
            return response.text # Return text if response isn't JSON
        except Exception as e:
            print(f"An error occurred: {e}")
            return None
        
    def download_file(self, save_path, endpoint="download", params=None) -> bool:
        """
        Downloads a file/stream and saves it to disk.
        :param endpoint: The API path
        :param save_path: Where to save the file
        """
        url = self._get_full_url(endpoint)
        
        # stream=True ensures we don't download the whole file into RAM at once
        try:
            with self.session.get(url, params=params, stream=True) as response:
                response.raise_for_status()

                # 2. Open a local file in 'wb' (Write Binary) mode
                with open(save_path, 'wb') as f:
                    # 3. Iterate over the stream in chunks (e.g., 8KB at a time)
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

            print(f"File saved to {save_path}")
            return True
        except requests.exceptions.HTTPError as err:
            print(f"HTTP Error: {err}")
        except Exception as e:
            print(f"An error occurred: {e}")

        return False