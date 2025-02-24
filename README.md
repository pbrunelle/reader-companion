# Reader Companion

Ask your companion LLM questions while reading PDFs.

# Usage

## Install

Install required packages:

```bash
pip install pyside6 httpx PyMuPDF
```

Download a forked version of pdf.js. This is necessary because we get some errors about url.parse when running viewer.html otherwise.

```bash
cd ..
git clone https://github.com/pbrunell/pdf.js.git
cd pdf.js
npm install
# Do we also need `npm install url`?
```

## Run

```bash
python3 reader-companion.py  --pdf-viewer .../pdf.js/web/viewer.html --file examples/2404.16130v2.pdf --settings examples/settings.json
```

## Troubleshooting


