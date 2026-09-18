import requests
import zipfile

url = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-4-july-2026-june-2031/access-and-downloads/digital-boundary-files/SA4_2026_AUST_SHP_GDA2020.zip"

response = requests.get(url, timeout=60)

print(response.status_code)


if response.status_code == 200:
    with open("data/raw/SA4_2026_AUST_SHP_GDA2020.zip", "wb") as f:
        f.write(response.content)
    with zipfile.ZipFile("data/raw/SA4_2026_AUST_SHP_GDA2020.zip", "r") as zip_ref:
        zip_ref.extractall("data/raw/SA4_2026_AUST_SHP_GDA2020")