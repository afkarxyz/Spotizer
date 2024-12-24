import sys
import asyncio
import aiohttp
import os
from pathlib import Path
import json
import requests
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, 
                            QPushButton, QProgressBar, QFileDialog,
                            QRadioButton, QToolButton)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap, QCursor

def get_track_metadata(track_url):
    try:
        track_id = track_url.split("/")[-1].split("?")[0]
        
        headers = {
            'Host': 'api.spotifydown.com',
            'Referer': 'https://spotifydown.com/',
            'Origin': 'https://spotifydown.com',
        }
        
        response = requests.get(
            f"https://api.spotifydown.com/metadata/track/{track_id}",
            headers=headers
        )
        
        return response.json()
        
    except Exception as e:
        return None

def scrape_arl_codes():
    url = "https://www.techedubyte.com/arl-aids/"
    try:
        response = requests.get(url)
        pattern = r'<pre class="wp-block-code"><code>(.*?)</code></pre>'
        matches = re.findall(pattern, response.text, re.DOTALL)
        
        result = []
        for match in matches:
            cleaned_text = re.sub(r'\s+', '', match)
            result.append(cleaned_text)
        
        return ', '.join(result)
            
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

class ImageDownloader(QThread):
    finished = pyqtSignal(bytes)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    async def download_image(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as response:
                if response.status == 200:
                    return await response.read()
        return None
        
    def run(self):
        image_data = asyncio.run(self.download_image())
        if image_data:
            self.finished.emit(image_data)

class TrackInfoFetcher(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        try:
            result = get_track_metadata(self.url)
            if result:
                self.finished.emit(result)
            else:
                self.error.emit("Failed to fetch track information")
        except Exception as e:
            self.error.emit(str(e))

class DownloaderWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, track_info, output_dir, arl, filename_format):
        super().__init__()
        self.track_info = track_info
        self.output_dir = output_dir
        self.arl_list = [arl.strip() for arl in arl.split(',')]
        self.filename_format = filename_format

    def format_filename(self):
        title = self.track_info['title']
        artists = self.track_info['artists']
        
        if self.filename_format == "title_artist":
            formatted = f"{title} - {artists}"
        else:
            formatted = f"{artists} - {title}"
            
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            formatted = formatted.replace(char, '_')
            
        return formatted

    def download_track(self, arl):
        try:
            from pydeezer import Deezer
            from pydeezer.constants import track_formats
            
            self.progress.emit(5)

            isrc = self.track_info['isrc']
            headers = {"Accept-Encoding": "gzip, deflate"}
            cookies = {'arl': arl}
            
            self.progress.emit(15)
            
            response = requests.get(
                f'https://api.deezer.com/2.0/track/isrc:{isrc}',
                cookies=cookies,
                headers=headers,
            )
            if response.status_code != 200:
                raise Exception(f"Failed to get track info: {response.status_code}")
            
            self.progress.emit(25)
            
            track_info = response.json()
            track_id = track_info.get("id")
            if not track_id:
                raise Exception("Couldn't find song on Deezer")

            self.progress.emit(35)

            deezer = Deezer(arl=arl)
            track = deezer.get_track(track_id)
            
            self.progress.emit(45)

            filename = self.format_filename() + ".mp3"
            output_path = os.path.join(self.output_dir, filename)

            def progress_callback(current, total):
                if total > 0:
                    progress = 50 + int((current / total) * 45)
                    self.progress.emit(progress)

            track["download"](
                self.output_dir,
                quality=track_formats.MP3_128,
                filename=filename,
                with_lyrics=False,
                show_message=False,
                callback=progress_callback
            )
            
            self.progress.emit(100)
            return output_path

        except Exception as e:
            raise Exception(f"An error occurred: {str(e)}")

    def run(self):
        for i, arl in enumerate(self.arl_list):
            try:
                output_path = self.download_track(arl)
                self.finished.emit("Download Complete!")
                return
            except Exception as e:
                if i == len(self.arl_list) - 1:
                    self.error.emit(f"All ARL codes failed. Last error: {str(e)}")
                else:
                    print(f"ARL {i+1} failed, trying next...")
                    self.progress.emit(0)
        
        self.error.emit("All ARL codes failed. Please try different ARL codes.")

def get_application_path():
    if getattr(sys, 'frozen', False):
        return sys.executable if hasattr(sys, '_MEIPASS') else os.path.dirname(sys.executable)
    else:
        return os.path.abspath(__file__)

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, relative_path)

class SpotizerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spotizer")
        
        icon_path = resource_path("icon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        self.setFixedWidth(600)
        self.setFixedHeight(180)
        
        self.default_music_dir = str(Path.home() / "Music")
        if not os.path.exists(self.default_music_dir):
            os.makedirs(self.default_music_dir)
        
        self.track_info = None
        self.arl_codes = None
        self.current_arl_index = 0
        
        if getattr(sys, 'frozen', False):
            self.cache_file = os.path.join(os.path.dirname(sys.executable), '.spotizer')
        else:
            self.cache_file = os.path.join(os.path.dirname(__file__), '.spotizer')
            
        self.init_ui()
        self.load_cache()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.input_widget = QWidget()
        input_layout = QVBoxLayout(self.input_widget)
        input_layout.setSpacing(10)

        url_layout = QHBoxLayout()
        url_label = QLabel("Track URL:")
        url_label.setFixedWidth(100)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Please enter track URL")
        self.url_input.setClearButtonEnabled(True)
        clear_button = self.url_input.findChild(QToolButton)
        if clear_button:
            clear_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.url_input.textChanged.connect(self.validate_url)
        
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.fetch_button.setFixedWidth(100)
        self.fetch_button.setEnabled(False)
        self.fetch_button.clicked.connect(self.fetch_track_info)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.fetch_button)
        input_layout.addLayout(url_layout)

        arl_layout = QHBoxLayout()
        arl_label = QLabel("ARL:")
        arl_label.setFixedWidth(100)
        self.arl_input = QLineEdit()
        self.arl_input.setPlaceholderText("Please enter the ARL value or click Get ARL")
        self.get_arl_button = QPushButton("Get ARL")
        self.get_arl_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.get_arl_button.setFixedWidth(100)
        self.get_arl_button.clicked.connect(self.get_arl)
        arl_layout.addWidget(arl_label)
        arl_layout.addWidget(self.arl_input)
        arl_layout.addWidget(self.get_arl_button)
        input_layout.addLayout(arl_layout)

        dir_layout = QHBoxLayout()
        dir_label = QLabel("Output Directory:")
        dir_label.setFixedWidth(100)
        self.dir_input = QLineEdit(self.default_music_dir)
        self.dir_button = QPushButton("Browse")
        self.dir_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.dir_button.setFixedWidth(100)
        dir_layout.addWidget(dir_label)
        dir_layout.addWidget(self.dir_input)
        dir_layout.addWidget(self.dir_button)
        self.dir_button.clicked.connect(self.select_directory)
        input_layout.addLayout(dir_layout)

        format_save_layout = QHBoxLayout()
        format_label = QLabel("Filename Format:")
        format_label.setFixedWidth(100)
        self.format_title_artist = QRadioButton("Title - Artist")
        self.format_artist_title = QRadioButton("Artist - Title")
        self.format_title_artist.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.format_artist_title.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.format_title_artist.setChecked(True)

        format_save_layout.addWidget(format_label)
        format_save_layout.addWidget(self.format_title_artist)
        format_save_layout.addWidget(self.format_artist_title)
        format_save_layout.addStretch()

        self.save_button = QPushButton("Save")
        self.save_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.save_button.setFixedWidth(100)
        self.save_button.clicked.connect(self.save_settings)
        format_save_layout.addWidget(self.save_button)

        input_layout.addLayout(format_save_layout)

        self.main_layout.addWidget(self.input_widget)

        self.track_widget = QWidget()
        self.track_widget.hide()
        track_layout = QHBoxLayout(self.track_widget)
        track_layout.setContentsMargins(0, 0, 0, 0)
        track_layout.setSpacing(10)

        cover_container = QWidget()
        cover_layout = QVBoxLayout(cover_container)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        cover_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(100, 100)
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_layout.addWidget(self.cover_label)
        track_layout.addWidget(cover_container)

        track_details_container = QWidget()
        track_details_layout = QVBoxLayout(track_details_container)
        track_details_layout.setContentsMargins(0, 0, 0, 0)
        track_details_layout.setSpacing(2)
        track_details_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.title_label.setWordWrap(True)
        self.title_label.setMinimumWidth(400)
        
        self.artist_label = QLabel()
        self.artist_label.setStyleSheet("font-size: 12px;")
        self.artist_label.setWordWrap(True)
        self.artist_label.setMinimumWidth(400)

        self.album_label = QLabel()
        self.album_label.setStyleSheet("font-size: 12px;")
        self.album_label.setWordWrap(True)
        self.album_label.setMinimumWidth(400)

        self.release_date_label = QLabel()
        self.release_date_label.setStyleSheet("font-size: 12px;")
        self.release_date_label.setWordWrap(True)
        self.release_date_label.setMinimumWidth(400)

        track_details_layout.addWidget(self.title_label)
        track_details_layout.addWidget(self.artist_label)
        track_details_layout.addWidget(self.album_label)
        track_details_layout.addWidget(self.release_date_label)
        track_layout.addWidget(track_details_container, stretch=1)
        track_layout.addStretch()

        self.main_layout.addWidget(self.track_widget)

        self.download_button = QPushButton("Download")
        self.download_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.download_button.setFixedWidth(100)
        self.download_button.clicked.connect(self.button_clicked)
        self.download_button.hide()

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.cancel_button.setFixedWidth(100)
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.cancel_button.hide()

        self.open_button = QPushButton("Open")
        self.open_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.open_button.setFixedWidth(100)
        self.open_button.clicked.connect(self.open_output_directory)
        self.open_button.hide()

        download_layout = QHBoxLayout()
        download_layout.addStretch()
        download_layout.addWidget(self.download_button)
        download_layout.addWidget(self.open_button)
        download_layout.addWidget(self.cancel_button)
        download_layout.addStretch()
        self.main_layout.addLayout(download_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.main_layout.addWidget(self.status_label)

    def validate_url(self, url):
        url = url.strip()
        
        self.fetch_button.setEnabled(False)
        
        if not url:
            self.status_label.clear()
            return
                
        if "open.spotify.com/" not in url:
            self.status_label.setText("Please enter a valid Spotify URL")
            return
                
        if "/album/" in url:
            self.status_label.setText("Album URLs are not supported. Please enter a track URL.")
            return
                
        if "/playlist/" in url:
            self.status_label.setText("Playlist URLs are not supported. Please enter a track URL.")
            return
                
        if "/track/" not in url:
            self.status_label.setText("Please enter a valid Spotify track URL")
            return
                
        self.fetch_button.setEnabled(True)
        self.status_label.clear()
    
    def fetch_track_info(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a Track URL")
            return

        self.fetch_button.setEnabled(False)
        self.status_label.setText("Fetching track information...")
        
        self.fetcher = TrackInfoFetcher(url)
        self.fetcher.finished.connect(self.handle_track_info)
        self.fetcher.error.connect(self.handle_fetch_error)
        self.fetcher.start()

    def handle_track_info(self, info):
        self.track_info = info
        self.fetch_button.setEnabled(True)
        
        title = info['title']
        artists = info['artists']
        album = info['album']
        release_date = info['releaseDate']
        date_parts = release_date.split('-')
        formatted_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
        
        self.title_label.setText(f"{title}")
        self.artist_label.setText(f"<b>{'Artists' if ',' in artists else 'Artist'}</b>    {artists}")
        self.album_label.setText(f"<b>Album</b>    {album}")
        self.release_date_label.setText(f"<b>Release Date</b>    {formatted_date}")
        
        image_url = info['cover']
        self.image_downloader = ImageDownloader(image_url)
        self.image_downloader.finished.connect(self.update_cover_art)
        self.image_downloader.start()
        
        self.input_widget.hide()
        self.track_widget.show()
        self.download_button.show()
        self.cancel_button.show()
        self.status_label.clear()

    def update_cover_art(self, image_data):
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        scaled_pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.cover_label.setPixmap(scaled_pixmap)

    def handle_fetch_error(self, error):
        self.fetch_button.setEnabled(True)
        self.status_label.setText(f"Error fetching track info: {error}")

    def select_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.dir_input.setText(directory)

    def open_output_directory(self):
        output_dir = self.dir_input.text().strip() or self.default_music_dir
        os.startfile(output_dir)

    def get_arl(self):
        self.get_arl_button.setEnabled(False)
        self.status_label.setText("Fetching ARL codes...")
        
        self.arl_codes = scrape_arl_codes()
        if self.arl_codes:
            self.arl_input.setText(self.arl_codes)
            self.status_label.setText("ARL codes fetched successfully.")
        else:
            self.status_label.setText("Failed to fetch ARL codes. Please try again.")
        
        self.get_arl_button.setEnabled(True)

    def cancel_clicked(self):
        self.track_widget.hide()
        self.input_widget.show()
        self.download_button.hide()
        self.cancel_button.hide()
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_label.clear()
        self.track_info = None
        self.fetch_button.setEnabled(True)

    def clear_form(self):
        self.url_input.clear()
        self.progress_bar.hide()
        self.progress_bar.setValue(0)
        self.status_label.clear()
        self.download_button.setText("Download")
        self.download_button.hide()
        self.cancel_button.hide()
        self.open_button.hide()
        self.track_widget.hide()
        self.input_widget.show()
        self.track_info = None

    def button_clicked(self):
        if self.download_button.text() == "Clear":
            self.clear_form()
        else:
            self.start_download()

    def start_download(self):
        if not self.track_info:
            self.status_label.setText("Please fetch track information first")
            return

        output_dir = self.dir_input.text().strip()
        if not output_dir:
            output_dir = self.default_music_dir
            self.dir_input.setText(output_dir)

        arl = self.arl_input.text().strip()
        if not arl:
            self.status_label.setText("Please enter or fetch ARL")
            return

        self.download_button.hide()
        self.cancel_button.hide()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("Downloading...")

        filename_format = "title_artist" if self.format_title_artist.isChecked() else "artist_title"

        self.worker = DownloaderWorker(
            self.track_info,
            output_dir,
            arl,
            filename_format
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.download_finished)
        self.worker.error.connect(self.download_error)
        self.worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def download_finished(self, message):
        self.progress_bar.hide()
        self.status_label.setText(message)
        self.download_button.setText("Clear")
        self.download_button.show()
        self.open_button.show()
        self.cancel_button.hide()
        self.download_button.setEnabled(True)

    def download_error(self, error_message):
        self.progress_bar.hide()
        self.status_label.setText(error_message)
        self.download_button.setText("Retry")
        self.download_button.show()
        self.cancel_button.show()
        self.download_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def save_settings(self):
        settings = {
            'arl': self.arl_input.text(),
            'output_dir': self.dir_input.text(),
            'filename_format': 'title_artist' if self.format_title_artist.isChecked() else 'artist_title'
        }
        with open(self.cache_file, 'w') as f:
            json.dump(settings, f)
        self.status_label.setText("Settings saved successfully.")

    def load_cache(self):
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'r') as f:
                settings = json.load(f)
            self.arl_input.setText(settings.get('arl', ''))
            self.dir_input.setText(settings.get('output_dir', self.default_music_dir))
            if settings.get('filename_format') == 'artist_title':
                self.format_artist_title.setChecked(True)
            else:
                self.format_title_artist.setChecked(True)

def main():
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    
    app = QApplication(sys.argv)
    window = SpotizerGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
