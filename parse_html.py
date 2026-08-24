from html.parser import HTMLParser

class MyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
    def handle_data(self, data):
        data = data.strip()
        if data:
            self.text.append(data)

with open('/Users/till/.gemini/antigravity/brain/2b6cbf22-6cb6-4904-b4f4-5af8793c560c/.system_generated/steps/1673/content.md', 'r') as f:
    html = f.read()
    
parser = MyHTMLParser()
parser.feed(html)
print('\n'.join(parser.text))
