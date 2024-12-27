import sys
import asyncio
import aiohttp
import os
from pathlib import Path
import requests
import re
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                            QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                            QProgressBar, QFileDialog, QRadioButton,
                            QListWidget)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QSettings
from PyQt6.QtGui import QIcon, QPixmap, QCursor

def get_metadata(url):
    try:
        if "/track/" in url:
            track_id = url.split("/")[-1].split("?")[0]
            
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
        else:
            response = requests.get(f"https://api.fabdl.com/spotify/get?url={url}")
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

class MetadataFetcher(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        try:
            result = get_metadata(self.url)
            if result:
                self.finished.emit(result)
            else:
                self.error.emit("Failed to fetch metadata")
        except Exception as e:
            self.error.emit(str(e))

class DownloaderWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    status_update = pyqtSignal(str)

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
            
        formatted = self.sanitize_filename(formatted)
        return self.output_dir, formatted

    def sanitize_filename(self, filename):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename

    def file_exists(self, filepath):
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0

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

            output_folder, filename = self.format_filename()
            output_path = os.path.join(output_folder, filename + ".mp3")

            if self.file_exists(output_path):
                self.status_update.emit(f"Skipping '{filename}' - File already exists")
                self.progress.emit(100)
                return output_path, True

            deezer = Deezer(arl=arl)
            track = deezer.get_track(track_id)
            
            self.progress.emit(45)

            def progress_callback(current, total):
                if total > 0:
                    progress = 50 + int((current / total) * 45)
                    self.progress.emit(progress)

            track["download"](
                output_folder,
                quality=track_formats.MP3_128,
                filename=filename + ".mp3",
                with_lyrics=False,
                show_message=False,
                callback=progress_callback
            )
            
            self.progress.emit(100)
            return output_path, False

        except Exception as e:
            raise Exception(f"An error occurred: {str(e)}")

    def run(self):
        for i, arl in enumerate(self.arl_list):
            try:
                result = self.download_track(arl)
                if result:
                    output_path, was_skipped = result
                    if was_skipped:
                        self.finished.emit("File already exists - Skipped!")
                    else:
                        self.finished.emit("Download Complete!")
                    return
            except Exception as e:
                if i == len(self.arl_list) - 1:
                    self.error.emit(f"All ARL codes failed. Last error: {str(e)}")
                else:
                    print(f"ARL {i+1} failed, trying next...")
                    self.progress.emit(0)

class AlbumPlaylistWindow(QMainWindow):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url
        self.tracks_data = []
        self.download_queue = []
        self.current_download_index = 0
        self.setWindowTitle("Album" if "/album/" in url else "Playlist")
        self.init_ui()
        self.fetch_metadata()
        
    def init_ui(self):
        self.setFixedWidth(600)
        self.setEnabled(True)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.track_widget = QWidget()
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

        self.total_tracks_label = QLabel()
        self.total_tracks_label.setStyleSheet("font-size: 12px;")
        self.total_tracks_label.setWordWrap(True)
        self.total_tracks_label.setMinimumWidth(400)

        self.release_date_label = QLabel()
        self.release_date_label.setStyleSheet("font-size: 12px;")
        self.release_date_label.setWordWrap(True)
        self.release_date_label.setMinimumWidth(400)

        track_details_layout.addWidget(self.title_label)
        track_details_layout.addWidget(self.artist_label)
        track_details_layout.addWidget(self.total_tracks_label)
        track_details_layout.addWidget(self.release_date_label)
        track_layout.addWidget(track_details_container, stretch=1)
        track_layout.addStretch()

        self.main_layout.addWidget(self.track_widget)

        self.track_list = QListWidget()
        self.track_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.track_list.setMinimumHeight(200)
        self.main_layout.addWidget(self.track_list)

        self.progress_container = QWidget()
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(0, 10, 0, 10)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        progress_layout.addWidget(self.progress_bar)
        
        self.main_layout.addWidget(self.progress_container)

        buttons_layout = QHBoxLayout()
        
        button_width = 150
        
        self.download_selected_button = QPushButton("Download Selected")
        self.download_selected_button.setFixedWidth(button_width)
        self.download_selected_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.download_selected_button.clicked.connect(self.download_selected)
        
        self.download_all_button = QPushButton("Download All")
        self.download_all_button.setFixedWidth(button_width)
        self.download_all_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.download_all_button.clicked.connect(self.download_all)
        
        self.close_button = QPushButton("Close")
        self.close_button.setFixedWidth(button_width)
        self.close_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_button.clicked.connect(self.close)

        buttons_layout.addStretch()
        buttons_layout.addWidget(self.download_selected_button)
        buttons_layout.addWidget(self.download_all_button)
        buttons_layout.addWidget(self.close_button)
        buttons_layout.addStretch()
        
        self.main_layout.addLayout(buttons_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.main_layout.addWidget(self.status_label)

    def fetch_metadata(self):
        try:
            response = requests.get(f"https://api.fabdl.com/spotify/get?url={self.url}")
            data = response.json()
            
            if 'result' not in data:
                self.status_label.setText("Error: Invalid response from API")
                return
                
            result = data['result']
            
            image_url = result.get('image')
            if image_url:
                self.download_cover_art(image_url)
            
            self.title_label.setText(f"{result.get('name', 'N/A')}")
            
            if '/playlist/' in self.url:
                owner = result.get('owner', 'Unknown')
                self.artist_label.setText(f"<b>Owner</b>    {owner}")
            else:
                artists = result.get('artists', 'N/A').replace(' & ', ', ')
                is_multiple_artists = ',' in artists
                artist_label = 'Artists' if is_multiple_artists else 'Artist'
                self.artist_label.setText(f"<b>{artist_label}</b>    {artists}")
            
            self.tracks_data = result.get('tracks', [])
            self.total_tracks_label.setText(f"<b>Total Tracks</b>    {len(self.tracks_data)}")
            
            self.track_list.clear()
            for i, track in enumerate(self.tracks_data, 1):
                duration = track.get('duration_ms', 0)
                minutes = duration // 60000
                seconds = (duration % 60000) // 1000
                duration_str = f"{minutes}:{seconds:02d}"
                
                track_artists = track['artists'].replace(' & ', ', ')
                
                self.track_list.addItem(f"{i}. {track['name']} - {track_artists} - {duration_str}")
            
            if 'releaseDate' in result:
                date = result['releaseDate']
                date_parts = date.split('-')
                formatted_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
                self.release_date_label.setText(f"<b>Release Date</b>    {formatted_date}")
            
        except Exception as e:
            self.status_label.setText(f"Error fetching metadata: {str(e)}")

    def download_cover_art(self, url):
        try:
            response = requests.get(url)
            if response.status_code == 200:
                pixmap = QPixmap()
                pixmap.loadFromData(response.content)
                scaled_pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, 
                                            Qt.TransformationMode.SmoothTransformation)
                self.cover_label.setPixmap(scaled_pixmap)
        except Exception as e:
            print(f"Error downloading cover art: {str(e)}")

    def get_track_metadata(self, track_id):
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

    def download_selected(self):
        selected_items = self.track_list.selectedItems()
        if not selected_items:
            self.status_label.setText("Please select at least one track")
            return
        
        self.download_selected_button.hide()
        self.download_all_button.hide()
        self.close_button.hide()
        
        self.download_tracks([self.track_list.row(item) for item in selected_items])

    def download_all(self):
        self.download_selected_button.hide()
        self.download_all_button.hide()
        self.close_button.hide()
        
        self.download_tracks(range(len(self.tracks_data)))

    def download_tracks(self, indices):
        self.download_queue = indices
        self.current_download_index = 0
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.setText("Starting downloads...")
        
        self.download_next_track()

    def download_next_track(self):
        if self.current_download_index >= len(self.download_queue):
            self.progress_bar.hide()
            self.status_label.setText("All downloads completed!")
            return
            
        idx = self.download_queue[self.current_download_index]
        try:
            track_id = self.tracks_data[idx]['id']
            track_metadata = self.get_track_metadata(track_id)
            
            if track_metadata:
                base_output_dir = self.parent().dir_input.text().strip() or self.parent().default_music_dir
                collection_name = self.title_label.text()
                collection_folder = os.path.join(base_output_dir, self.sanitize_filename(collection_name))
                
                if not os.path.exists(collection_folder):
                    os.makedirs(collection_folder)
                
                self.worker = DownloaderWorker(
                    track_metadata,
                    collection_folder,
                    self.parent().arl_input.text().strip(),
                    "title_artist" if self.parent().format_title_artist.isChecked() else "artist_title"
                )
                
                self.worker.progress.connect(
                    lambda p: self.update_progress(p, self.current_download_index, len(self.download_queue))
                )
                self.worker.error.connect(self.handle_download_error)
                self.worker.finished.connect(self.handle_track_complete)
                self.worker.status_update.connect(self.status_label.setText)
                self.worker.start()
                
        except Exception as e:
            self.status_label.setText(f"Error downloading track {idx + 1}: {str(e)}")
            self.handle_track_complete("Error")

    def sanitize_filename(self, filename):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        return filename

    def update_progress(self, current_progress, current_track, total_tracks):
        track_weight = 100.0 / total_tracks
        base_progress = (current_track * track_weight)
        track_progress = (current_progress * track_weight) / 100.0
        total_progress = int(base_progress + track_progress)
        
        self.progress_bar.setValue(total_progress)
        self.status_label.setText(f"Downloading track {current_track + 1} of {total_tracks}...")
        QApplication.processEvents()

    def handle_track_complete(self, message):
        self.current_download_index += 1
        
        if self.current_download_index >= len(self.download_queue):
            self.download_selected_button.show()
            self.download_all_button.show()
            self.close_button.show()
        
        QTimer.singleShot(100, self.download_next_track)

    def handle_download_error(self, error):
        self.status_label.setText(f"Download error: {error}")
        
        self.download_selected_button.show()
        self.download_all_button.show()
        self.close_button.show()
        
        self.handle_track_complete("Error")

class SpotizerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spotizer")
        
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        self.setFixedWidth(600)
        self.setFixedHeight(180)
        
        self.default_music_dir = str(Path.home() / "Music")
        if not os.path.exists(self.default_music_dir):
            os.makedirs(self.default_music_dir)
        
        self.track_info = None
        self.arl_codes = None
        
        self.settings = QSettings('Spotizer', 'Settings')
        self.init_ui()
        self.load_settings()
        self.setup_auto_save()
        
    def setup_auto_save(self):
        self.arl_input.textChanged.connect(self.auto_save_settings)
        self.dir_input.textChanged.connect(self.auto_save_settings)
    
    def auto_save_settings(self):
        self.settings.setValue('arl', self.arl_input.text())
        self.settings.setValue('output_dir', self.dir_input.text())
        self.settings.sync()
        
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(10, 10, 10, 10)

        self.input_widget = QWidget()
        input_layout = QVBoxLayout(self.input_widget)
        input_layout.setSpacing(10)

        url_layout = QHBoxLayout()
        url_label = QLabel("Spotify URL:")
        url_label.setFixedWidth(100)
        
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Please enter the Spotify URL")
        self.url_input.setClearButtonEnabled(True)
        
        self.fetch_button = QPushButton("Fetch")
        self.fetch_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.fetch_button.setFixedWidth(100)
        self.fetch_button.clicked.connect(self.fetch_metadata)
        url_layout.addWidget(url_label)
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(self.fetch_button)
        input_layout.addLayout(url_layout)

        arl_layout = QHBoxLayout()
        arl_label = QLabel("ARL:")
        arl_label.setFixedWidth(100)
        self.arl_input = QLineEdit()
        self.arl_input.setPlaceholderText("Please enter the ARL value or click Get ARL")
        self.arl_input.setClearButtonEnabled(True)
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
        self.format_title_artist.toggled.connect(self.on_radio_toggled)

        format_save_layout.addWidget(format_label)
        format_save_layout.addWidget(self.format_title_artist)
        format_save_layout.addWidget(self.format_artist_title)
        format_save_layout.addStretch()

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
        download_layout.addWidget(self.open_button)
        download_layout.addWidget(self.download_button)
        download_layout.addWidget(self.cancel_button)
        download_layout.addStretch()
        self.main_layout.addLayout(download_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        self.main_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.main_layout.addWidget(self.status_label)

    def fetch_metadata(self):
        url = self.url_input.text().strip()
        if not url:
            self.status_label.setText("Please enter a URL")
            return

        if "/album/" in url or "/playlist/" in url:
            self.album_window = AlbumPlaylistWindow(url, self)
            self.album_window.show()
            return

        self.fetch_button.setEnabled(False)
        self.status_label.setText("Fetching track information...")
        
        self.fetcher = MetadataFetcher(url)
        self.fetcher.finished.connect(self.handle_track_info)
        self.fetcher.error.connect(self.handle_fetch_error)
        self.fetcher.start()

    def handle_track_info(self, info):
        if 'error' in info:
            self.status_label.setText(info['error'])
            self.fetch_button.setEnabled(True)
            return
            
        self.track_info = info
        self.fetch_button.setEnabled(True)
        
        title = info['title']
        artists = info['artists'].replace(' & ', ', ')
        album = info['album']
        release_date = info['releaseDate']
        date_parts = release_date.split('-')
        formatted_date = f"{date_parts[2]}-{date_parts[1]}-{date_parts[0]}"
        
        self.title_label.setText(f"{title}")
        is_multiple_artists = ',' in artists
        artist_label = 'Artists' if is_multiple_artists else 'Artist'
        self.artist_label.setText(f"<b>{artist_label}</b>    {artists}")
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
        self.worker.status_update.connect(self.status_label.setText)
        self.worker.start()

    def update_progress(self, value):
        self.progress_bar.setValue(value)

    def download_finished(self, message):
        self.progress_bar.hide()
        self.status_label.setText(message)
        self.open_button.show()
        self.download_button.setText("Clear")
        self.download_button.show()
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

    def on_radio_toggled(self, checked):
        if checked:
            self.settings.setValue('filename_format', 'title_artist')
        else:
            self.settings.setValue('filename_format', 'artist_title')
        self.settings.sync()

    def save_settings(self):
        self.settings.setValue('arl', self.arl_input.text())
        self.settings.setValue('output_dir', self.dir_input.text())
        self.settings.sync()
        self.status_label.setText("Settings saved successfully.")

    def load_settings(self):
        self.arl_input.setText(self.settings.value('arl', '', str))
        self.dir_input.setText(self.settings.value('output_dir', self.default_music_dir, str))
        
        format_setting = self.settings.value('filename_format', 'title_artist')
        self.format_title_artist.setChecked(format_setting == 'title_artist')
        self.format_artist_title.setChecked(format_setting == 'artist_title')

def main():
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    
    app = QApplication(sys.argv)
    window = SpotizerGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
