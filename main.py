from core import *
from config import *

def main():
    fetcher = Fetcher(STORE_NODE)
    store = Store(fetcher)
    while True:
        request = input("endpoint param1,param2...")

        endpoint, params = request.split()
        info = fetcher.get(endpoint)

        print(store.update(info) if isinstance(info, dict) else info)

if __name__ == "__main__":
    main()