# main.py (Toga - The Ultimate Control Version)

import toga
from toga.style import Pack
from toga.style.pack import COLUMN, ROW, CENTER
from toga.colors import BLACK, WHITE, DODGERBLUE, LIGHTGRAY, BLUE, RED
from toga.fonts import BOLD
import threading
import os
import time
import yt_dlp
import imageio_ffmpeg
import requests
import sys
import logging
import re
import tempfile

# إعداد التسجيل للتصحيح
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    # محاولة استيراد مكتبة الإشعارات للهواتف
    from plyer import notification
    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False
    notification = None

DOWNLOADS_FOLDER = os.path.join(os.path.expanduser('~'), 'Downloads')

class DownloadManager:
    """مدير التحميل للتحكم في العمليات الخلفية"""
    _active_downloads = {}
    _cancel_flags = {}
    
    @classmethod
    def add_download(cls, download_id, thread):
        cls._active_downloads[download_id] = thread
        cls._cancel_flags[download_id] = False
        
    @classmethod
    def remove_download(cls, download_id):
        if download_id in cls._active_downloads:
            del cls._active_downloads[download_id]
        if download_id in cls._cancel_flags:
            del cls._cancel_flags[download_id]
            
    @classmethod
    def cancel_download(cls, download_id):
        if download_id in cls._cancel_flags:
            cls._cancel_flags[download_id] = True
            
    @classmethod
    def should_cancel(cls, download_id):
        return cls._cancel_flags.get(download_id, False)
            
    @classmethod
    def get_active_downloads(cls):
        return cls._active_downloads

class ProgressTracker:
    """تتبع تقدم التحميل"""
    def __init__(self, app, download_id):
        self.app = app
        self.download_id = download_id
        self.last_update = time.time()
        
    def hook(self, d):
        # التحقق من طلب الإلغاء
        if DownloadManager.should_cancel(self.download_id):
            raise Exception("Download cancelled by user")
            
        if d['status'] == 'downloading':
            # تحديث كل 0.3 ثانية لتجنب إبطاء الواجهة
            current_time = time.time()
            if current_time - self.last_update > 0.3:
                percent = d.get('_percent_str', '0%').strip()
                speed = d.get('_speed_str', 'N/A')
                eta = d.get('_eta_str', 'N/A')
                
                # تنظيف النص من الأحرف غير الضرورية
                percent = percent.replace('[download]', '').strip()
                
                # إرسال التحديث إلى الواجهة الرئيسية
                self.app.main_thread_update(lambda p=percent, s=speed, e=eta: 
                                          self.app.update_progress(p, s, e))
                self.last_update = current_time
        return True

class TogaDownloader(toga.App):

    def startup(self):
        self.main_box = toga.Box(style=Pack(direction=COLUMN, background_color=BLACK))

        # --- الشاشة الرئيسية ---
        title_label = toga.Label("Downloader", style=Pack(font_size=24, font_weight=BOLD, color=DODGERBLUE, text_align=CENTER, margin=20))
        self.url_input = toga.TextInput(placeholder='Paste a link here...', style=Pack(flex=1, padding=5, background_color=LIGHTGRAY, color=BLACK))
        paste_button = toga.Button('Paste', on_press=self.paste_from_clipboard, style=Pack(width=80, padding=5))
        self.download_button_main = toga.Button('Download', on_press=self.go_to_download_screen, style=Pack(flex=1, padding=10, background_color=DODGERBLUE, color=WHITE, font_weight=BOLD))
        
        exit_button = toga.Button('Exit', on_press=self.exit_app, style=Pack(flex=1, padding=10, background_color='red', color=WHITE, font_weight=BOLD))
        
        input_box = toga.Box(children=[self.url_input, paste_button], style=Pack(direction=ROW, padding=5))
        self.main_screen_box = toga.Box(children=[title_label, input_box, self.download_button_main, exit_button], style=Pack(direction=COLUMN, padding=10))

        # --- شاشة التحميل ---
        self.thumbnail_image = toga.ImageView(style=Pack(width=320, height=180, margin_top=20, align_items=CENTER, background_color=BLACK))
        self.title_result_label = toga.Label("Video title", style=Pack(padding=10, text_align=CENTER, font_weight=BOLD, color=WHITE))
        self.rename_input = toga.TextInput(placeholder="Enter new name (optional)", style=Pack(flex=1, margin_bottom=10))
        
        self.mp4_button = toga.Button('MP4', on_press=lambda w: self.select_format('mp4'), style=Pack(flex=1))
        self.mp3_button = toga.Button('MP3', on_press=lambda w: self.select_format('mp3'), style=Pack(flex=1))
        self.format_box = toga.Box(children=[self.mp4_button, self.mp3_button], style=Pack(direction=ROW, margin_top=10))
        self.quality_spinner = toga.Selection(items=['Select format first'], style=Pack(flex=1, padding=5))
        
        self.start_download_button = toga.Button('Start Download', on_press=self.start_download, style=Pack(flex=1, padding=10, background_color=DODGERBLUE, color=WHITE, font_weight=BOLD))
        
        # شريط التقدم والنسبة المئوية
        self.progress_container = toga.Box(style=Pack(direction=COLUMN, padding=10, visibility='hidden'))
        self.percentage_label = toga.Label("0%", style=Pack(font_size=24, text_align=CENTER, color=BLUE, font_weight=BOLD))
        self.status_label = toga.Label("", style=Pack(padding=5, text_align=CENTER, color=LIGHTGRAY))
        self.speed_label = toga.Label("", style=Pack(padding=5, text_align=CENTER, color=LIGHTGRAY))
        
        # زر إلغاء التحميل
        self.cancel_button = toga.Button('Cancel Download', on_press=self.cancel_download, style=Pack(flex=1, padding=10, background_color=RED, color=WHITE, font_weight=BOLD, visibility='hidden'))
        
        self.progress_container.add(self.percentage_label)
        self.progress_container.add(self.status_label)
        self.progress_container.add(self.speed_label)
        self.progress_container.add(self.cancel_button)
        
        another_video_button = toga.Button('Download Another Video', on_press=self.go_to_main_screen, style=Pack(flex=1, padding=5))
        exit_button_download = toga.Button('Exit', on_press=self.exit_app, style=Pack(flex=1, padding=10, background_color='red', color=WHITE, font_weight=BOLD))

        self.download_controls_box = toga.Box(
            children=[self.rename_input, self.format_box, self.quality_spinner, self.start_download_button],
            style=Pack(direction=COLUMN)
        )

        self.download_screen_box = toga.Box(
            children=[
                self.thumbnail_image,
                self.title_result_label,
                self.download_controls_box,
                self.progress_container,
                another_video_button,
                exit_button_download
            ],
            style=Pack(direction=COLUMN, padding=10)
        )

        self.main_window = toga.MainWindow(title=self.formal_name)
        self.main_window.content = self.main_box
        self.go_to_main_screen()
        self.main_window.show()

        self.video_info = {}
        self.current_download_id = None
        self.is_downloading = False
        self.download_thread = None
        self.progress_tracker = None
        
        self.check_clipboard_for_url()

    def switch_screen(self, new_screen_box):
        if self.main_box.children:
            self.main_box.remove(self.main_box.children[0])
        self.main_box.add(new_screen_box)

    def go_to_main_screen(self, widget=None):
        self.url_input.value = ''
        self.switch_screen(self.main_screen_box)

    def go_to_download_screen(self, widget):
        url = self.url_input.value.strip()
        if not url:
            self.main_window.error_dialog("Input Error", "Please provide a link to download.")
            return
        
        self.download_button_main.enabled = False
        self.download_button_main.text = 'Fetching Info...'
        
        threading.Thread(target=self.fetch_video_info, args=(url,), daemon=True).start()

    def fetch_video_info(self, url):
        try:
            ydl_opts = {
                'quiet': True, 
                'no_warnings': True,
                'socket_timeout': 30,
                'extract_flat': False
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                self.video_info = ydl.extract_info(url, download=False)
            self.main_thread_update(lambda: self.display_download_screen())
        except Exception as e:
            error_msg = f"Failed to fetch info: {str(e)}"
            logger.error(error_msg)
            self.main_thread_update(lambda: self.show_error(error_msg))

    def display_download_screen(self):
        """عرض شاشة التحميل"""
        title = self.video_info.get('title', 'N/A')
        self.title_result_label.text = title
        self.rename_input.value = ''
        
        thumbnail_url = self.video_info.get('thumbnail')
        if thumbnail_url:
            threading.Thread(target=self.load_thumbnail, args=(thumbnail_url,), daemon=True).start()

        self.select_format('mp4') # عرض جودات الفيديو افتراضياً
        self.reset_download_ui()
        self.switch_screen(self.download_screen_box)
        self.download_button_main.enabled = True
        self.download_button_main.text = 'Download'

    def load_thumbnail(self, url):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            image_data = response.content
            
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
                tmp_file.write(image_data)
                tmp_file.flush()
                self.main_thread_update(lambda path=tmp_file.name: self.set_thumbnail_image(path))
        except Exception as e:
            logger.error(f"Failed to load thumbnail: {e}")

    def set_thumbnail_image(self, image_path):
        try:
            self.thumbnail_image.image = toga.Image(image_path)
        except Exception as e:
            logger.error(f"Toga failed to display image: {e}")

    def paste_from_clipboard(self, widget):
        try:
            if pyperclip:
                self.url_input.value = pyperclip.paste()
            else:
                self.main_window.info_dialog("Clipboard Error", "Pyperclip library not found. Please paste manually.")
        except Exception as e:
            logger.error(f"Could not access clipboard: {e}")
            self.main_window.info_dialog("Clipboard Error", "Could not access clipboard. Please paste manually.")

    def check_clipboard_for_url(self):
        try:
            if pyperclip:
                clipboard_text = pyperclip.paste()
                if clipboard_text and ('http://' in clipboard_text or 'https://' in clipboard_text or 'youtube.com' in clipboard_text or 'youtu.be' in clipboard_text):
                    self.url_input.value = clipboard_text
        except Exception as e:
            logger.error(f"Could not check clipboard: {e}")

    def select_format(self, format_type):
        formats_list = []
        
        if format_type == 'mp4':
            formats_list.append({'text': 'Best Quality (Video + Audio)', 'id': 'bestvideo+bestaudio'})
            unique_heights = set()
            for f in self.video_info.get('formats', []):
                if f.get('vcodec') != 'none' and f.get('height', 0) > 0 and f['height'] not in unique_heights:
                    unique_heights.add(f['height'])
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    label = f"{f['height']}p"
                    if filesize > 0:
                        label += f" ({(filesize / 1024**2):.1f} MB)"
                    formats_list.append({'text': f"Video Only: {label}", 'id': f['format_id']})
                        
        elif format_type == 'mp3':
            unique_abr = set()
            for f in self.video_info.get('formats', []):
                if f.get('acodec') != 'none' and f.get('vcodec') == 'none' and f.get('abr', 0) > 0 and f['abr'] not in unique_abr:
                    unique_abr.add(f['abr'])
                    filesize = f.get('filesize') or f.get('filesize_approx') or 0
                    label = f"{f['abr']}kbps"
                    if filesize > 0:
                        label += f" ({(filesize / 1024**2):.1f} MB)"
                    formats_list.append({'text': label, 'id': f['format_id']})
            if not formats_list:
                formats_list.append({'text': 'Best Audio', 'id': 'bestaudio'})
        
        if not formats_list:
            formats_list.append({'text': 'Default Quality', 'id': 'best'})
            
        self.quality_spinner.items = [f['text'] for f in formats_list]
        self.quality_spinner.format_map = {f['text']: f['id'] for f in formats_list}
        
        if formats_list:
            self.quality_spinner.value = formats_list[0]['text']

    def start_download(self, widget):
        selected_quality_text = self.quality_spinner.value
        if not selected_quality_text or selected_quality_text == 'Select format first':
            self.main_window.error_dialog("Selection Error", "Please select a quality.")
            return
            
        format_id = self.quality_spinner.format_map.get(selected_quality_text)
        if not format_id:
            self.main_window.error_dialog("Selection Error", "Could not find format ID.")
            return

        self.download_controls_box.style.visibility = 'hidden'
        self.progress_container.style.visibility = 'visible'
        self.cancel_button.style.visibility = 'visible'
        self.status_label.text = "Downloading..."
        
        url = self.video_info.get('webpage_url')
        custom_filename = self.rename_input.value.strip()
        
        self.current_download_id = f"{url}_{format_id}_{time.time()}"
        self.is_downloading = True
        
        # إنشاء متتبع التقدم
        self.progress_tracker = ProgressTracker(self, self.current_download_id)
        
        self.download_thread = threading.Thread(
            target=self.download_thread_target, 
            args=(url, format_id, custom_filename, self.current_download_id),
            daemon=False
        )
        
        DownloadManager.add_download(self.current_download_id, self.download_thread)
        self.download_thread.start()

    def cancel_download(self, widget):
        """إلغاء التحميل الحالي"""
        if self.is_downloading and self.current_download_id:
            DownloadManager.cancel_download(self.current_download_id)
            self.status_label.text = "Cancelling download..."
            self.cancel_button.enabled = False

    def update_progress(self, percent, speed, eta):
        """تحديث شريط التقدم مع النسبة المئوية"""
        self.percentage_label.text = percent
        self.speed_label.text = f"{speed} - ETA: {eta}"
        
    def get_unique_filename(self, base_path, extension):
        counter = 1
        new_path = f"{base_path}.{extension}"
        while os.path.exists(new_path):
            new_path = f"{base_path} ({counter}).{extension}"
            counter += 1
        return new_path

    def download_thread_target(self, url, format_id, custom_filename, download_id):
        try:
            os.makedirs(DOWNLOADS_FOLDER, exist_ok=True)
            
            base_name = custom_filename or re.sub(r'[<>:"/\\|?*]', '', self.video_info.get('title', 'download'))
            
            # تحديد نوع التحميل بناءً على format_id
            is_audio_only = format_id == 'bestaudio' or 'mp3' in self.quality_spinner.value.lower()

            if is_audio_only:
                # خيارات تحميل الصوت فقط
                extension = 'mp3'
                final_path = self.get_unique_filename(os.path.join(DOWNLOADS_FOLDER, base_name), extension)
                ydl_opts = {
                    'format': format_id if format_id != 'bestaudio' else 'bestaudio/best',
                    'outtmpl': final_path,
                    'extractaudio': True,
                    'audioformat': 'mp3',
                    'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'progress_hooks': [self.progress_tracker.hook],
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '192',
                    }]
                }
            else:
                # خيارات تحميل الفيديو (مع دمج الصوت)
                extension = 'mp4'
                final_path = self.get_unique_filename(os.path.join(DOWNLOADS_FOLDER, base_name), extension)
                
                # استخدام format_id المحدد بدلاً من الخيار الافتراضي
                if format_id == 'bestvideo+bestaudio':
                    format_string = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
                else:
                    format_string = format_id
                
                ydl_opts = {
                    'format': format_string,
                    'outtmpl': final_path,
                    'merge_output_format': 'mp4',
                    'ffmpeg_location': imageio_ffmpeg.get_ffmpeg_exe(),
                    'noplaylist': True,
                    'quiet': True,
                    'no_warnings': True,
                    'progress_hooks': [self.progress_tracker.hook],
                }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                
            # التحقق إذا كان التحميل ملغى
            if DownloadManager.should_cancel(download_id):
                # حذف الملف إذا كان التحميل ملغى
                try:
                    if os.path.exists(final_path):
                        os.remove(final_path)
                except:
                    pass
                self.main_thread_update(lambda: self.show_error("Download cancelled"))
                return
                
            title = self.video_info.get('title', 'Download')
            self.send_notification("Download Complete", f"'{title}' has been downloaded successfully")
            
            self.main_thread_update(lambda: self.show_success("Download Complete!"))

        except Exception as e:
            if "cancelled" in str(e).lower():
                self.main_thread_update(lambda: self.show_error("Download cancelled"))
            else:
                error_msg = f"An error occurred:\n{str(e)}"
                logger.error(error_msg)
                self.main_thread_update(lambda: self.show_error(error_msg))
        finally:
            self.is_downloading = False
            DownloadManager.remove_download(download_id)

    def send_notification(self, title, message):
        try:
            if PLYER_AVAILABLE:
                notification.notify(title=title, message=message, timeout=10)
            else:
                self.main_thread_update(lambda: self.main_window.info_dialog(title, message))
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    def show_error(self, message):
        # استخدام الطريقة الآمنة لعرض الديالوج
        def show_dialog():
            self.main_window.error_dialog("Error", str(message))
            self.reset_main_ui()
        self.main_thread_update(show_dialog)

    def show_success(self, message):
        # استخدام الطريقة الآمنة لعرض الديالوج
        def show_dialog():
            self.main_window.info_dialog("Success", message)
            self.reset_download_ui()
        self.main_thread_update(show_dialog)

    def reset_main_ui(self):
        self.download_button_main.enabled = True
        self.download_button_main.text = 'Download'
        self.go_to_main_screen()

    def reset_download_ui(self):
        self.download_controls_box.style.visibility = 'visible'
        self.progress_container.style.visibility = 'hidden'
        self.cancel_button.style.visibility = 'hidden'
        self.cancel_button.enabled = True
        self.percentage_label.text = "0%"
        self.status_label.text = ""
        self.speed_label.text = ""
        self.is_downloading = False
        
    def main_thread_update(self, func):
        # استخدام الطريقة الآمنة لتحديث الواجهة
        try:
            self.main_window.app._impl.loop.call_soon_threadsafe(func)
        except:
            # بديل إذا فشلت الطريقة الأولى
            try:
                import asyncio
                asyncio.get_event_loop().call_soon_threadsafe(func)
            except:
                # محاولة تنفيذ الدالة مباشرة
                try:
                    func()
                except Exception as e:
                    logger.error(f"Failed to update UI: {e}")

    def exit_app(self, widget):
        if self.is_downloading:
            # استخدام الطريقة الآمنة لعرض الديالوج
            def show_confirm():
                result = self.main_window.confirm_dialog(
                    "Download in Progress", 
                    "A download is in progress. Closing the app will interrupt it. Are you sure you want to exit?"
                )
                if result:
                    self.main_window.close()
            self.main_thread_update(show_confirm)
        else:
            self.main_window.close()

def main():
    return TogaDownloader('Downloader', 'org.example.downloader')

if __name__ == '__main__':
    app = main()
    try:
        app.main_loop()
    except Exception as e:
        logger.error(f"App crashed: {e}")
    finally:
        active_downloads = DownloadManager.get_active_downloads()
        for download_id, thread in list(active_downloads.items()):
            if thread.is_alive():
                thread.join(timeout=2)