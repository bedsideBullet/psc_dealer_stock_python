import sys
import sqlite3
import csv
import io
from datetime import datetime
from ftplib import FTP

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem,
    QHeaderView, QPushButton, QLineEdit, QVBoxLayout, QWidget,
    QLabel, QMessageBox, QProgressBar, QComboBox, QHBoxLayout,
    QFileDialog, QInputDialog, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap


DB_NAME = "dealer_stock.db"

# ============================================================
# HARD CODED FTP SERVERS - EDIT THESE
# ============================================================
FTP_SERVERS = {
    "Summit": {
        "host": "ftp.summit.com",           # <-- CHANGE THIS
        "port": 21,
        "username": "your_username",        # <-- CHANGE THIS
        "password": "your_password"         # <-- CHANGE THIS
    },
    "Turn5": {
        "host": "ftp.turn5.com",            # <-- CHANGE THIS
        "port": 21,
        "username": "your_username",        # <-- CHANGE THIS
        "password": "your_password"         # <-- CHANGE THIS
    }
}





# ============================================================


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    c.execute("""CREATE TABLE IF NOT EXISTS stock (
                 id INTEGER PRIMARY KEY AUTOINCREMENT,
                 part_number TEXT UNIQUE,
                 in_stock INTEGER,
                 date_time TEXT
                 )""")
    
    c.execute("""CREATE TABLE IF NOT EXISTS ftp_last_upload (
                 server_name TEXT PRIMARY KEY,
                 last_upload TEXT
                 )""")

    # Auto-import stock data if table is empty
    c.execute("SELECT COUNT(*) FROM stock")
    if c.fetchone()[0] == 0:
        try:
            import json
            with open('clean_db.json', 'r', encoding='utf-8') as f:
                data = json.load(f)
            today = datetime.now().strftime("%m/%d/%Y")
            for item in data.get('stock', []):
                c.execute("""
                    INSERT OR IGNORE INTO stock (part_number, in_stock, date_time)
                    VALUES (?, ?, ?)
                """, (item['Part Number'], item['IN Stock'], item.get('Date Time', today)))
            print("Auto-imported stock data from clean_db.json")
        except FileNotFoundError:
            print("clean_db.json not found – starting with empty stock")
        except Exception as e:
            print(f"Error importing data: {e}")

    conn.commit()
    conn.close()


def get_stock():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, part_number, in_stock, date_time FROM stock ORDER BY part_number")
    rows = c.fetchall()
    conn.close()
    return rows


def update_stock(item_id, quantity, date_time=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    if date_time:
        c.execute("UPDATE stock SET in_stock=?, date_time=? WHERE id=?", (quantity, date_time, item_id))
    else:
        c.execute("UPDATE stock SET in_stock=? WHERE id=?", (quantity, item_id))
    conn.commit()
    conn.close()


def add_part(part_number, quantity=0):
    today = datetime.now().strftime("%m/%d/%Y")
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO stock (part_number, in_stock, date_time) VALUES (?,?,?)",
                  (part_number, quantity, today))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    conn.close()


def delete_part(item_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM stock WHERE id=?", (item_id,))
    conn.commit()
    conn.close()


class FTPUploadThread(QThread):
    progress = pyqtSignal(int)
    finished_sig = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config, stock_data):
        super().__init__()
        self.config = config
        self.stock_data = stock_data

    def run(self):
        try:
            today = datetime.now().strftime("%m/%d/%Y")
            total = len(self.stock_data)

            for i, item in enumerate(self.stock_data):
                update_stock(item[0], item[2], today)
                self.progress.emit(int((i + 1) / total * 70))

            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["Part Number", "In Stock", "Date Time"])
            for item in self.stock_data:
                writer.writerow([item[1], item[2], today])

            csv_data = output.getvalue().encode('utf-8')

            ftp = FTP()
            ftp.connect(self.config["host"], self.config["port"])
            ftp.login(self.config["username"], self.config["password"])

            total_size = len(csv_data)
            uploaded = 0

            def callback(data):
                nonlocal uploaded
                uploaded += len(data)
                self.progress.emit(70 + int(uploaded / total_size * 30))

            ftp.storbinary("STOR PSC_Stock.csv", io.BytesIO(csv_data), 1024, callback)
            ftp.quit()

            self.progress.emit(100)
            self.finished_sig.emit(f"Uploaded to {self.config['name']} on {today}")
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dealer Stock Management")
        self.resize(1200, 800)

        self.current_page = 1
        self.items_per_page = 25
        self.search_term = ""

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ========== HEADER ==========
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(40, 30, 40, 20)

        self.original_pixmap = QPixmap("logo.png")
        self.logo_label = QLabel()
        if self.original_pixmap.isNull():
            self.logo_label.setText("[Logo Missing]")
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.logo_label)

        header_layout.addStretch()

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()
        layout.addWidget(header_container)

        # ========== CONTROLS ==========
        controls = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search Part Number...")
        self.search_input.textChanged.connect(self.on_search)
        controls.addWidget(self.search_input)

        controls.addStretch()

        self.add_btn = QPushButton("Add Part")
        self.add_btn.clicked.connect(self.add_part)
        controls.addWidget(self.add_btn)

        self.export_btn = QPushButton("Export CSV")
        self.export_btn.clicked.connect(self.export_csv)
        controls.addWidget(self.export_btn)

        # ========== HARD CODED FTP BUTTONS ==========
        # Summit Button
        summit_container = QWidget()
        summit_vlayout = QVBoxLayout(summit_container)
        summit_vlayout.setContentsMargins(8, 5, 8, 5)
        summit_vlayout.setSpacing(4)

        self.summit_btn = QPushButton("Summit")
        self.summit_btn.setFixedHeight(38)
        self.summit_btn.clicked.connect(lambda: self.upload_ftp("Summit"))
        summit_vlayout.addWidget(self.summit_btn)

        self.summit_last_label = QLabel("Last: Never")
        self.summit_last_label.setStyleSheet("color: #cccccc; font-size: 10pt;")
        self.summit_last_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        summit_vlayout.addWidget(self.summit_last_label)

        controls.addWidget(summit_container)

        # Turn 5 Button
        turn5_container = QWidget()
        turn5_vlayout = QVBoxLayout(turn5_container)
        turn5_vlayout.setContentsMargins(8, 5, 8, 5)
        turn5_vlayout.setSpacing(4)

        self.turn5_btn = QPushButton("Turn 5")
        self.turn5_btn.setFixedHeight(38)
        self.turn5_btn.clicked.connect(lambda: self.upload_ftp("Turn5"))
        turn5_vlayout.addWidget(self.turn5_btn)

        self.turn5_last_label = QLabel("Last: Never")
        self.turn5_last_label.setStyleSheet("color: #cccccc; font-size: 10pt;")
        self.turn5_last_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        turn5_vlayout.addWidget(self.turn5_last_label)

        controls.addWidget(turn5_container)

        layout.addLayout(controls)

        # ========== TABLE ==========
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Part Number", "In Stock", "Date Time", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self.table.itemChanged.connect(self.on_item_changed)
        layout.addWidget(self.table)

        # ========== PAGINATION ==========
        pag_layout = QHBoxLayout()
        pag_layout.addWidget(QLabel("Items per page:"))
        self.per_page_combo = QComboBox()
        self.per_page_combo.addItems(["10", "25", "50", "100"])
        self.per_page_combo.setCurrentText("25")
        self.per_page_combo.currentTextChanged.connect(self.on_per_page_change)
        pag_layout.addWidget(self.per_page_combo)
        pag_layout.addStretch()

        self.prev_btn = QPushButton("Previous")
        self.prev_btn.clicked.connect(self.prev_page)
        pag_layout.addWidget(self.prev_btn)

        self.page_label = QLabel()
        pag_layout.addWidget(self.page_label)

        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(self.next_page)
        pag_layout.addWidget(self.next_btn)

        layout.addLayout(pag_layout)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.load_last_upload_times()
        self.load_data()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'original_pixmap'):
            return

        limiting_size = min(self.width(), self.height())

        logo_size = max(60, min(int(limiting_size * 0.15), 180))
        if not self.original_pixmap.isNull():
            scaled_logo = self.original_pixmap.scaled(
                logo_size * 2, logo_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.logo_label.setPixmap(scaled_logo)

        base_font_size = max(20, min(int(limiting_size / 20), 30))
        date_font_size = max(14, base_font_size - 8)

        self.title_label.setText(
            f"<div style='font-size:{base_font_size}pt; font-weight:bold; color:white;'>Dealer Stock Management</div>"
            f"<div style='font-size:{date_font_size}pt; color:#aaaaaa; margin-top:15px;'>Today's Date: {datetime.now().strftime('%m/%d/%Y')}</div>"
        )

    def load_last_upload_times(self):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT server_name, last_upload FROM ftp_last_upload")
        last_uploads = dict(c.fetchall())
        conn.close()

        self.summit_last_label.setText(f"Last: {last_uploads.get('Summit', 'Never')}")
        self.turn5_last_label.setText(f"Last: {last_uploads.get('Turn5', 'Never')}")

    def load_data(self):
        stock = get_stock()
        filtered = [row for row in stock if self.search_term.lower() in row[1].lower()]
        filtered.sort(key=lambda x: x[1])

        total = len(filtered)
        pages = max(1, (total + self.items_per_page - 1) // self.items_per_page)
        self.current_page = min(self.current_page, pages)

        start = (self.current_page - 1) * self.items_per_page
        end = start + self.items_per_page
        page_data = filtered[start:end]

        self.table.setRowCount(len(page_data))
        for r, row in enumerate(page_data):
            self.table.setItem(r, 0, QTableWidgetItem(row[1]))
            qty_item = QTableWidgetItem(str(row[2]))
            qty_item.setData(Qt.ItemDataRole.UserRole, row[0])
            self.table.setItem(r, 1, qty_item)
            self.table.setItem(r, 2, QTableWidgetItem(row[3]))

            remove_btn = QPushButton("Remove")
            remove_btn.clicked.connect(lambda _, rid=row[0]: self.remove_part(rid))
            self.table.setCellWidget(r, 3, remove_btn)

        self.page_label.setText(f"Page {self.current_page} of {pages}")
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < pages)

    def on_search(self, text):
        self.search_term = text
        self.current_page = 1
        self.load_data()

    def on_per_page_change(self, text):
        self.items_per_page = int(text)
        self.current_page = 1
        self.load_data()

    def prev_page(self):
        self.current_page -= 1
        self.load_data()

    def next_page(self):
        self.current_page += 1
        self.load_data()

    def on_item_changed(self, item):
        if item.column() == 1:
            try:
                qty = int(item.text() or 0)
            except ValueError:
                qty = 0
            item_id = item.data(Qt.ItemDataRole.UserRole)
            update_stock(item_id, qty)

    def add_part(self):
        part, ok = QInputDialog.getText(self, "Add Part", "Part Number:")
        if ok and part.strip():
            add_part(part.strip())
            self.load_data()
            QMessageBox.information(self, "Success", "Part added")

    def remove_part(self, item_id):
        reply = QMessageBox.question(self, "Confirm", "Delete this part?")
        if reply == QMessageBox.StandardButton.Yes:
            delete_part(item_id)
            self.load_data()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", "PSC_Stock.csv", "CSV Files (*.csv)")
        if path:
            stock = get_stock()
            today = datetime.now().strftime("%m/%d/%Y")
            for item in stock:
                update_stock(item[0], item[2], today)

            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Part Number", "In Stock", "Date Time"])
                for item in stock:
                    writer.writerow([item[1], item[2], today])
            QMessageBox.information(self, "Success", "CSV exported")

    def upload_ftp(self, name):
        config = FTP_SERVERS.get(name)
        if not config:
            QMessageBox.warning(self, "Error", f"No configuration found for {name}")
            return

        config = config.copy()
        config["name"] = name

        stock = get_stock()
        if not stock:
            QMessageBox.warning(self, "Error", "No data to upload")
            return

        self.progress.setVisible(True)
        self.progress.setValue(0)

        self.thread = FTPUploadThread(config, stock)
        self.thread.progress.connect(self.progress.setValue)
        self.thread.finished_sig.connect(lambda msg: (
            self.update_last_upload(name),
            QMessageBox.information(self, "Success", msg),
            self.progress.setVisible(False),
            self.load_data()
        ))
        self.thread.error.connect(lambda msg: (
            QMessageBox.critical(self, "Error", msg),
            self.progress.setVisible(False)
        ))
        self.thread.start()

    def update_last_upload(self, server_name):
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""
            INSERT INTO ftp_last_upload (server_name, last_upload)
            VALUES (?, ?)
            ON CONFLICT(server_name) DO UPDATE SET last_upload=excluded.last_upload
        """, (server_name, now))
        conn.commit()
        conn.close()
        self.load_last_upload_times()


if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("PSC_favicon.jpg"))
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())