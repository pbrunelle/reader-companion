from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTextEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QWidget,
    QSplitter
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QEvent
import httpx
from typing import Any
import json
import fitz
import base64
import os  
import argparse
from pydantic import BaseModel
from typing import Literal

class AppSettings(BaseModel):
    model: str = "gemini-1.5-flash"
    max_output_tokens: int = 1000
    history: bool = False
    send_pdf: Literal["whole_images", "whole_pdf", "single_page", "false"] = "whole_images"
    system_prompt_no_whole_pdf: str
    system_prompt_whole_pdf: str
    font_size: int = 12

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
        self._httpx_client = httpx.Client(timeout=30)
        self._api_key = os.environ["GEMINI_API_KEY"]

    def send(
        self,
        query: str,
        images: list[str] | None,
        pdf_uploaded_file_uri: str | None,
        history: list[dict[str, str]] | None
    ) -> dict[str, Any]:
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
        if pdf_uploaded_file_uri:
            d = {
                "file_data": {
                    "mime_type": f"application/pdf",
                    "file_uri": pdf_uploaded_file_uri,
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
        print(f"Prompting Gemini: {url=} {len(str(data))=} {len(contents)=}")
        response = self._httpx_client.post(url, json=data)
        print(f"Received response: {response=} {response.json()=}")
        response.raise_for_status()
        return response

class GeminiWorker(QThread):
    response_received = Signal(str, str)
    error_occurred = Signal(str)

    def __init__(
        self, 
        query: str, 
        pdf_images: list[str] | None,
        pdf_uploaded_file_uri: str | None,
        app_settings: AppSettings,
        history: list[dict[str, str]] | None,
    ):
        super().__init__()
        self.query = query
        self.pdf_images = pdf_images
        self.pdf_uploaded_file_uri = pdf_uploaded_file_uri
        self.app_settings = app_settings
        self.history = history
    
    def run(self):
        gemini = Gemini(
            model=self.app_settings.model,
            system_prompt=(
                self.app_settings.system_prompt_whole_pdf
                if self.app_settings.send_pdf in ("whole_images", "whole_pdf")
                else self.app_settings.system_prompt_no_whole_pdf
            ),
            max_output_tokens=self.app_settings.max_output_tokens,
        )
        try:
            response = gemini.send(self.query, self.pdf_images, self.pdf_uploaded_file_uri, self.history).json()
        except (httpx.HTTPError, json.decoder.JSONDecodeError) as e:
            self.error_occurred.emit(f"ERROR: {type(e)} {str(e)}")
            return
        try:
            text = response["candidates"][0]["content"]["parts"][0]["text"]
            self.response_received.emit(self.query, text)
        except (KeyError, IndexError) as e:
            self.error_occurred.emit(f"ERROR: {type(e)} {str(e)} {response}")

def upload_pdf_to_goole(filename: str) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    with open(filename, "rb") as f:
        pdf_bytes = f.read()
    num_bytes = len(pdf_bytes)
    # Initial resumable request defining metadata.
    url = f"https://generativelanguage.googleapis.com/upload/v1beta/files?key={api_key}"
    d = {
        "file": {
            "display_name": "TEXT",
        }
    }
    headers = {
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(num_bytes),
        "X-Goog-Upload-Header-Content-Type": "application/pdf",
    }
    print(f"Sending: {url=} {d=} {headers=}")
    response = httpx.post(url, headers=headers, json=d)
    print(f"Received: {response=} {response.content=} {response.headers=}")
    response.raise_for_status()
    # Get URL to upload to
    url = response.headers["x-goog-upload-url"]
    # Upload the actual bytes
    headers = {
        "Content-Length": str(num_bytes),
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
    }
    print(f"Sending: {url=} {headers=}")
    response = httpx.post(url, headers=headers, content=pdf_bytes)
    print(f"Received: {response=} {response.content=} {response.headers=}")
    response.raise_for_status()
    d = response.json()
    file_uri = d["file"]["uri"]
    return file_uri
    
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

def get_pdf_bytes(pdf_path: str) -> bytes:
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

class ReaderCompanion(QMainWindow):
    def __init__(self, pdf_viewer: str, filename: str, settings_file: str):
        super().__init__()
        self.setWindowTitle("Reader Companion")
        self.qsettings = QSettings("PierreBrunelle", "ReaderCompanion")
        self.pdf_viewer = os.path.abspath(pdf_viewer).replace("\\", "/")
        self.filename = os.path.abspath(filename)
        self.settings_file = settings_file
        self.pdf_images = None
        self.pdf_uploaded_file_uri = None
        self.history = None
        self.view = QWebEngineView(self)
        self.view.page().selectionChanged.connect(self.copy_to_input)
        self.view.page().loadFinished.connect(self.set_sidebar_status)
        self.sidebar_open = None
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
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.view)
        self.splitter.addWidget(right_widget)
        main_layout = QHBoxLayout()
        main_layout.addWidget(self.splitter)
        central = QWidget()
        central.setLayout(main_layout)
        self.setCentralWidget(central)
        app_settings = self.get_settings()
        if "font_size" in app_settings:
            font = self.input.font()
            font.setPointSize(app_settings["font_size"])
            self.input.setFont(font)
            self.send.setFont(font)
            self.output.setFont(font)
        self.apply_qsettings()
        url = f"file:///{self.pdf_viewer}/web/viewer.html?file={self.filename}"
        print(f"{url=}")
        self.view.load(url)
    
    def apply_qsettings(self) -> None:
        geometry = self.qsettings.value("geometry", defaultValue=self.saveGeometry())
        self.restoreGeometry(geometry)
        splitter_sizes = self.qsettings.value("splitter_sizes", defaultValue=[700, 300], type=list)
        self.splitter.setSizes([int(x) for x in splitter_sizes])
        self.sidebar_open = self.qsettings.value("sidebar_open", defaultValue=None, type=int)

    def save_qsettings(self) -> None:
        self.qsettings.setValue("geometry", self.saveGeometry())
        self.qsettings.setValue("splitter_sizes", self.splitter.sizes())
        if self.sidebar_open is not None:
            self.qsettings.setValue("sidebar_open", self.sidebar_open)
        self.qsettings.sync()
    
    def get_sidebar_status_then_save(self) -> None:
        self.view.page().runJavaScript("PDFViewerApplication.pdfSidebar.isOpen", self.handle_get_sidebar_status_then_save)
    
    def handle_get_sidebar_status_then_save(self, result) -> None:
        self.sidebar_open = int(result)
        self.save_qsettings()

    def set_sidebar_status(self) -> None:
        if self.sidebar_open is not None:
            js = f"PDFViewerApplicationOptions.set('sidebarViewOnLoad', {int(self.sidebar_open)})"
            self.view.page().runJavaScript(js, self.handle_set_sidebar_status)

    def handle_set_sidebar_status(self, result) -> None:
        pass

    def closeEvent(self, event: QEvent) -> None:
        self.get_sidebar_status_then_save()
        return super().closeEvent(event)

    def get_settings(self) -> AppSettings:            
        with open(self.settings_file, "rt") as f:
            return AppSettings.model_validate_json(f.read())
        
    def copy_to_input(self):
        self.view.page().runJavaScript("window.getSelection().toString();", self.set_text_input)
    
    def set_text_input(self, result):
        if result:
            self.input.setText(result)

    def send_to_gemini(self, *args, **kwargs):
        self.output.setText("Waiting ...")
        app_settings = self.get_settings()
        if app_settings.send_pdf == "whole_images":
            if not self.pdf_images:
                print("Getting PDF images once")
                self.pdf_images = get_pdf_images(self.filename)
                total_bytes = sum([len(pix) for pix in self.pdf_images])
                print(f"Got {len(self.pdf_images)} pages {total_bytes} bytes")
        else:
            self.pdf_images = None
        if app_settings.send_pdf == "whole_pdf":
            if not self.pdf_uploaded_file_uri:
                print("Uploading PDF to Google once")
                self.pdf_uploaded_file_uri = upload_pdf_to_goole(self.filename)
        else:
            self.pdf_uploaded_file_uri = None
        if app_settings.history:
            if not self.history:
                self.history = []
        else:
            self.history = None
        text = self.input.toPlainText()
        self.thread = GeminiWorker(text, self.pdf_images, self.pdf_uploaded_file_uri, app_settings, self.history)
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
