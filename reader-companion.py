from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QWidget,
    QSplitter
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import Qt, QThread, Signal
import httpx
from typing import Any
import json
import fitz
import base64
import os  
import argparse

class Gemini:
    def __init__(
        self,
        model: str,
        system_prompt: str,
        max_output_tokens: int,
    ):
        self._model = model
        self._system_prompt = system_prompt
        self._max_output_tokens = max_output_tokens
        self._httpx_client = httpx.Client()
        self._api_key = os.environ["GEMINI_API_KEY"]

    def send(self, query: str, images: list[str] | None, history: list[dict[str, str]] | None) -> dict[str, Any]:
        contents = []
        if history:
            for h in history:
                contents.append({"role": h["role"], "parts": [{"text": h["text"]}]})
        parts = []
        if images:
            for pix in images:
                d = {
                    "inline_data": {
                        "mime_type": f"image/png",
                        "data": pix,
                    }
                }
                parts.append(d)
        parts.append({"text": query})
        contents.append({"role": "user", "parts": parts})
        data = {
            "generationConfig": {
                "max_output_tokens": self._max_output_tokens,
            },
            "system_instruction": {
                "parts": {"text": self._system_prompt}
            },
            "contents": contents,
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent?key={self._api_key}"
        # print(f"Sending to Gemini: {url=} {data=}")
        response = self._httpx_client.post(url, json=data)
        print(f"Received response: {response=} {response.json()=}")
        response.raise_for_status()
        return response

class GeminiWorker(QThread):
    response_received = Signal(str, str)
    error_occurred = Signal(str)

    def __init__(self, query: str, pdf_images: list[str] | None, settings: dict[str, Any], history: list[dict[str, str]] | None):
        super().__init__()
        self.query = query
        self.pdf_images = pdf_images
        self.settings = settings
        self.history = history
    
    def run(self):
        gemini = Gemini(
            model=self.settings["model"],
            system_prompt=(
                self.settings["system_prompt_whole_pdf"]
                if self.settings["whole_pdf"]
                else self.settings["system_prompt_no_whole_pdf"]
            ),
            max_output_tokens=self.settings["max_output_tokens"],
        )
        try:
            response = gemini.send(self.query, self.pdf_images, self.history).json()
        except (httpx.HTTPError, json.decoder.JSONDecodeError) as e:
            self.error_occurred.emit(f"ERROR: {type(e)} {str(e)}")
            return
        try:
            text = response["candidates"][0]["content"]["parts"][0]["text"]
            self.response_received.emit(self.query, text)
        except (KeyError, IndexError) as e:
            self.error_occurred.emit(f"ERROR: {type(e)} {str(e)} {response}")
        
def get_pdf_images(pdf_path: str) -> list[str]:
    """From a PDF document converts all of its pages tp images and return the b64 encoding, one per page."""
    ret = []
    doc = fitz.open(pdf_path)
    for page_num in range(doc.page_count):
        page = doc[page_num]
        pix = page.get_pixmap()
        pix_bytes = pix.tobytes()
        pix_b64 = base64.b64encode(pix_bytes).decode("utf-8")
        ret.append(pix_b64)
    return ret

class ReaderCompanion(QMainWindow):
    def __init__(self, pdf_viewer: str, filename: str, settings_file: str):
        super().__init__()
        self.setWindowTitle("Reader Companion")
        self.pdf_viewer = os.path.abspath(pdf_viewer).replace("\\", "/")
        self.filename = os.path.abspath(filename)
        self.settings_file = settings_file
        with open(settings_file, "rt") as f:
            settings = json.loads(f.read())
        self.pdf_images = None
        self.history = None
        self.view = QWebEngineView(self)
        self.view.page().selectionChanged.connect(self.copy_to_input)
        self.input = QTextEdit()
        self.input.setPlaceholderText("Select from PDF or type here ...")
        self.send = QPushButton("Ask Your Reader Companion")
        self.send.clicked.connect(self.send_to_gemini)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        right_layout = QVBoxLayout()
        right_layout.addWidget(self.input, 2)
        right_layout.addWidget(self.send)
        right_layout.addWidget(self.output, 7)
        right_widget = QWidget()
        right_widget.setLayout(right_layout)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.view)
        splitter.addWidget(right_widget)
        splitter.setSizes([700, 300])
        main_layout = QHBoxLayout()
        main_layout.addWidget(splitter)
        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)
        settings = self.get_settings()
        if "font_size" in settings:
            font = self.input.font()
            font.setPointSize(settings["font_size"])
            self.input.setFont(font)
            self.send.setFont(font)
            self.output.setFont(font)
        url = f"file:///{self.pdf_viewer}?file={self.filename}"
        print(f"{url=}")
        self.view.load(url)
        self.showMaximized()
    
    def get_settings(self):
        with open(self.settings_file, "rt") as f:
            return json.loads(f.read())
        
    def copy_to_input(self):
        self.view.page().runJavaScript("window.getSelection().toString();", self.set_text_input)
    
    def set_text_input(self, result):
        if result:
            self.input.setText(result)

    def send_to_gemini(self, *args, **kwargs):
        self.output.setText("Waiting ...")
        settings = self.get_settings()
        if settings["whole_pdf"]:
            if not self.pdf_images:
                print("Getting PDF images once")
                self.pdf_images = get_pdf_images(self.filename)
                total_bytes = sum([len(pix) for pix in self.pdf_images])
                print(f"Got {len(self.pdf_images)} pages {total_bytes} bytes")
        else:
            self.pdf_images = None
        if settings["history"]:
            if not self.history:
                self.history = []
        else:
            self.history = None
        text = self.input.toPlainText()
        self.thread = GeminiWorker(text, self.pdf_images, settings, self.history)
        self.thread.response_received.connect(self.handle_gemini_response)
        self.thread.error_occurred.connect(self.handle_gemini_error)
        self.thread.start()
    
    def handle_gemini_response(self, query, text):
        self.output.setMarkdown(text)
        if self.history is not None:
            self.history.append({"text": query, "role": "user"})
            self.history.append({"text": text, "role": "model"})
    
    def handle_gemini_error(self, err):
        self.output.setText(err)

def parse_args() -> Any:
    parser = argparse.ArgumentParser(prog='reader-companion')
    parser.add_argument('--file')
    parser.add_argument('--pdf-viewer')
    parser.add_argument('--settings')
    args = parser.parse_args()
    return args

if __name__ == '__main__':
    app = QApplication([])
    args = parse_args()
    print(args)
    window = ReaderCompanion(args.pdf_viewer, args.file, args.settings)
    window.show()
    app.exec()
