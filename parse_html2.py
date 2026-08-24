import json, sys
with open("parsed_docs.txt", "r") as f:
    html = f.read()
import re
from bs4 import BeautifulSoup
soup = BeautifulSoup(html, 'html.parser')
print(soup.get_text()[:2000])
