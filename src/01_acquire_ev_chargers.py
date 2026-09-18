import requests
import os

url = "https://opendata.transport.nsw.gov.au/data/dataset/be1c4de4-4517-4bd0-8a09-2965ddfc7179/resource/7bbb6461-e52d-4fe7-ace4-a15c30198de0/download/ev_20251216.csv"

response = requests.get(url, timeout=30)

print(response.status_code)
print(response.headers.get("content-type"))
if not os.path.exists("data/raw"):
    os.makedirs("data/raw")
if response.status_code == 200:
    with open("data/raw/ev_20251216.csv", "wb") as f:
        f.write(response.content)

else:
    print(f"Failed to download the file. Status code: {response.status_code}")
    