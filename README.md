# Reader Companion

Ask questions to your LLM companion while reading PDFs.

![image](https://github.com/user-attachments/assets/8606ac65-feba-46a7-875d-40139ea728dc)

# Usage

## Install

Install required packages:

```bash
pip install pyside6 httpx PyMuPDF
```

Download a forked version of pdf.js. This is necessary because we get some errors about url.parse when running viewer.html otherwise.

```bash
cd ..
git clone https://github.com/pbrunelle/pdf.js.git
cd pdf.js
npm install
# Do we also need `npm install url`?
```

## Run

```bash
export GEMINI_API_KEY=...
python3 reader-companion.py  --pdf-viewer .../pdf.js --file examples/2404.16130v2.pdf --settings examples/settings.json
```

## Troubleshooting


