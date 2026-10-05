import os
import sys
import tempfile
import subprocess
import threading
import pythoncom

import numpy as np
import soundcard as sc
import soundfile as sf

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


SAMPLE_RATE = 48000
CHANNELS = 2
MP3_BITRATE = "320k"


class RecorderWorker(QObject):
    finished = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.recording = False
        self.thread = None
        self.audio_data = None

    def start_recording(self):
        if self.recording:
            return

        self.recording = True

        self.thread = threading.Thread(
            target=self._record,
            daemon=True
        )
        self.thread.start()

    def stop_recording(self):
        self.recording = False

    def _record(self):
        com_initialized = False

        try:
            # initialize COM for this worker thread
            pythoncom.CoInitialize()
            com_initialized = True

            speaker = sc.default_speaker()

            if speaker is None:
                raise RuntimeError(
                    "No default audio device was found."
                )

            microphone = sc.get_microphone(
                speaker.id,
                include_loopback=True
            )

            chunks = []

            with microphone.recorder(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS
            ) as recorder:

                while self.recording:
                    data = recorder.record(
                        numframes=int(SAMPLE_RATE * 0.1)
                    )

                    if data is not None and len(data):
                        chunks.append(data.copy())

            if not chunks:
                raise RuntimeError(
                    "No audio was recorded."
                )

            self.audio_data = np.concatenate(
                chunks,
                axis=0
            )

            self.finished.emit()

        except Exception as e:
            self.recording = False
            self.error.emit(str(e))

        finally:
            if com_initialized:
                pythoncom.CoUninitialize()



class AudioRecorderWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.worker = RecorderWorker()

        self.is_recording = False
        self.temp_wav = None

        self.setWindowTitle("System Audio Recorder")
        self.setFixedSize(360, 180)

        # put app window in the foreground all the time
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self._create_ui()

        self.worker.finished.connect(self.recording_finished)
        self.worker.error.connect(self.recording_error)

    def _create_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(15)
        layout.setContentsMargins(25, 25, 25, 25)

        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)

        self.record_button = QPushButton("Record")
        self.record_button.setMinimumHeight(45)
        self.record_button.clicked.connect(self.toggle_recording)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setMinimumHeight(45)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_recording)

        layout.addWidget(self.status_label)
        layout.addWidget(self.record_button)
        layout.addWidget(self.stop_button)

        self.setLayout(layout)

    def toggle_recording(self):
        if not self.is_recording:
            self.start_recording()

    def start_recording(self):
        self.is_recording = True

        self.status_label.setText("● Recording ...")
        self.record_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        self.worker.start_recording()

    def stop_recording(self):
        if not self.is_recording:
            return

        self.is_recording = False

        self.status_label.setText("Stopping ...")
        self.stop_button.setEnabled(False)

        self.worker.stop_recording()

    def recording_finished(self):

        self.status_label.setText("Recording Finished.")

        self.record_button.setEnabled(False)

        # Save recording as a temporary .wav file
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".wav",
                delete=False
            ) as temp_file:
                self.temp_wav = temp_file.name

            sf.write(
                self.temp_wav,
                self.worker.audio_data,
                SAMPLE_RATE,
                subtype="PCM_16"
            )

        except Exception as e:
            self.cleanup_temp_file()

            QMessageBox.critical(
                self,
                "Error",
                f"Could not process recording:\n\n{e}"
            )

            self.reset_ui()
            return

        # open file explorer dialog
        self.save_recording()

    def save_recording(self):
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save recording",
            "recording.mp3",
            "MP3 Audio (*.mp3)"
        )

        # user clicked cancel
        if not file_path:
            self.cleanup_temp_file()
            self.reset_ui()
            return

        # add .mp3 file extension 
        if not file_path.lower().endswith(".mp3"):
            file_path += ".mp3"

        self.status_label.setText("Generating MP3 file ...")

        try:
            self.convert_to_mp3(
                self.temp_wav,
                file_path
            )

            self.cleanup_temp_file()

            QMessageBox.information(
                self,
                "Recording saved",
                f"The recording was saved:\n\n"
                f"{file_path}"
            )

            self.reset_ui()

        except FileNotFoundError:
            self.cleanup_temp_file()

            QMessageBox.critical(
                self,
                "FFmpeg not found",
                "FFmpeg was not found.\n\n"
                "Please install ffmpeg and make sure the, "
                "'ffmpeg' is callable as PATH environment variable."
            )

            self.reset_ui()

        except subprocess.CalledProcessError as e:
            self.cleanup_temp_file()

            QMessageBox.critical(
                self,
                "MP3-Error",
                "Could not convert the recording into a MP3 file.\n\n"
                f"FFmpeg-Error:\n{e}"
            )

            self.reset_ui()

        except Exception as e:
            self.cleanup_temp_file()

            QMessageBox.critical(
                self,
                "Error",
                f"Could not save recording:\n\n{e}"
            )

            self.reset_ui()

    @staticmethod
    def convert_to_mp3(input_wav, output_mp3):
        command = [
            "ffmpeg",
            "-y",

            # input
            "-i",
            input_wav,

            # force stereo
            "-ac",
            str(CHANNELS),

            # 48 kHz
            "-ar",
            str(SAMPLE_RATE),

            # MP3 with 320 kbit/s
            "-b:a",
            str(MP3_BITRATE),

            output_mp3
        ]

        subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

    def cleanup_temp_file(self):
        if self.temp_wav:
            try:
                if os.path.exists(self.temp_wav):
                    os.remove(self.temp_wav)
            except OSError:
                pass

            self.temp_wav = None

    def reset_ui(self):
        self.is_recording = False

        self.status_label.setText("Bereit")
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(False)

        self.worker.audio_data = None

    def recording_error(self, error_message):
        self.is_recording = False

        self.cleanup_temp_file()

        QMessageBox.critical(
            self,
            "Recording error",
            f"Could not start system audio recording:\n\n{error_message}"
        )

        self.reset_ui()

    def closeEvent(self, event):
        # stop recording when closing
        if self.is_recording:
            self.worker.stop_recording()

        self.cleanup_temp_file()

        event.accept()


def main():
    app = QApplication(sys.argv)

    app.setStyle("Fusion")

    window = AudioRecorderWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
