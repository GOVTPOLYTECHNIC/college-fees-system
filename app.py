from flask import Flask, render_template, request, redirect, url_for, session, send_file, jsonify
import sqlite3
from datetime import datetime
import os
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from io import BytesIO

# Optional PDF
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

# External libs
import qrcode
from PIL import Image
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Month helper
try:
    from dateutil.relativedelta import relativedelta
except Exception:
    relativedelta = None

# ---- CONFIG ----
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(APP_ROOT, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = 'your_secret_key'
DB = os.path.join(APP_ROOT, 'college.db')

FAST2SMS_API_KEY = "YOUR_FAST2SMS_API_KEY_HERE"
EMAIL_ADDRESS = "your.email@gmail.com"
EMAIL_APP_PASSWORD = "your_email_app_password"


# ---------------- Helper: DB ----------------
def get_db():
    conn = sqlite3.connect(DB, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# -------------- DB INIT ---------------
def init_db():
    with get_db() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            roll_no TEXT UNIQUE,
            course TEXT,
            year TEXT,
            email TEXT,
            phone TEXT,
            total_fee REAL DEFAULT 20000,
            photo TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS fees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            amount REAL,
            date TEXT,
            mode TEXT,
            remark TEXT
        )""")

        c.execute("SELECT COUNT(*) FROM users")
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                      ("admin", generate_password_hash("admin123")))

        conn.commit()


init_db()


# ------------ LOGIN REQUIRED -----------
from functools import wraps
def login_required(f):
    @wraps(f)
    def secure(*a, **k):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*a, **k)
    return secure


# ------------ LOGIN PAGE ---------------
@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM users WHERE username=?", (u,))
            user = c.fetchone()

            if user and check_password_hash(user['password'], p):
                session['user_id'] = user['id']
                session['username'] = user['username']
                return redirect(url_for('dashboard'))
            else:
                return render_template('login.html', error="Invalid username or password")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ------------ DASHBOARD --------------
@app.route('/')
@login_required
def dashboard():
    with get_db() as conn:
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM students")
        total_students = c.fetchone()[0]

        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees")
        total_collection = c.fetchone()[0]

        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE date LIKE ?", (today + "%",))
        today_collection = c.fetchone()[0]

        month = datetime.now().strftime("%Y-%m")
        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE date LIKE ?", (month + "%",))
        month_collection = c.fetchone()[0]

    return render_template("dashboard.html",
                           total_students=total_students,
                           total_collection=total_collection,
                           today_collection=today_collection,
                           month_collection=month_collection)
# ----------------- PART 2 (continue) -----------------

# -------------------- ID Card (QR) --------------------
@app.route('/student/<int:student_id>/idcard')
@login_required
def id_card(student_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE id=?", (student_id,))
        s = c.fetchone()

    if not s:
        return "Student not found"

    try:
        qr_data = f"ID:{s['id']} | ROLL:{s['roll_no']} | NAME:{s['name']}"
        qr = qrcode.make(qr_data)
        qr_path = os.path.join('static', 'uploads', f"qr_{s['id']}.png")
        qr.save(os.path.join(APP_ROOT, qr_path))
    except Exception as e:
        print("QR create error:", e)
        qr_path = None

    return render_template('id_card.html', s=s, qr_path=qr_path)


# -------------------- Admission CRUD --------------------------------------
@app.route('/admission', methods=['GET','POST'])
@login_required
def admission():
    if request.method == 'POST':
        name = request.form['name']
        roll_no = request.form['roll']
        course = request.form['course']
        year = request.form['year']
        email = request.form['email']
        phone = request.form['phone']
        total_fee = float(request.form.get('total_fee') or 20000)
        photo = request.files.get('photo')

        filename = None
        if photo and photo.filename:
            fname = secure_filename(photo.filename)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"{roll_no}_{timestamp}_{fname}"
            photo.save(os.path.join(UPLOAD_FOLDER, filename))

        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO students (name, roll_no, course, year, email, phone, total_fee, photo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, roll_no, course, year, email, phone, total_fee, filename)
                )
                conn.commit()

                try:
                    if phone:
                        send_sms(phone, f"Welcome {name}! Your admission is completed.")
                    if email:
                        send_email(email, "Admission Successful", f"Dear {name}, your admission is confirmed.")
                except Exception as e:
                    print("Notification send error (admission):", e)

            return redirect(url_for('students'))
        except sqlite3.IntegrityError:
            return "Roll number already exists!"
    return render_template('admission.html')


@app.route('/students')
@login_required
def students():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM students ORDER BY id DESC")
        all_students = c.fetchall()
    return render_template('students.html', students=all_students)


@app.route('/student/<int:student_id>/edit', methods=['GET','POST'])
@login_required
def edit_student(student_id):
    with get_db() as conn:
        c = conn.cursor()
        if request.method == 'POST':
            name = request.form['name']
            course = request.form['course']
            year = request.form['year']
            email = request.form['email']
            phone = request.form['phone']
            total_fee = float(request.form.get('total_fee') or 20000)
            photo = request.files.get('photo')

            if photo and photo.filename:
                fname = secure_filename(photo.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                filename = f"{student_id}_{timestamp}_{fname}"
                photo.save(os.path.join(UPLOAD_FOLDER, filename))
                conn.execute("UPDATE students SET name=?, course=?, year=?, email=?, phone=?, total_fee=?, photo=? WHERE id=?",
                             (name, course, year, email, phone, total_fee, filename, student_id))
            else:
                conn.execute("UPDATE students SET name=?, course=?, year=?, email=?, phone=?, total_fee=? WHERE id=?",
                             (name, course, year, email, phone, total_fee, student_id))
            conn.commit()
            return redirect(url_for('students'))

        c.execute("SELECT * FROM students WHERE id=?", (student_id,))
        s = c.fetchone()
    return render_template('edit_student.html', s=s)


@app.route('/student/<int:student_id>/delete', methods=['POST'])
@login_required
def delete_student(student_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM fees WHERE student_id=?", (student_id,))
        c.execute("DELETE FROM students WHERE id=?", (student_id,))
        conn.commit()
    return redirect(url_for('students'))


# -------------------- Fees Entry & List -----------------------------------
@app.route('/fees', methods=['GET','POST'])
@login_required
def fees():
    if request.method == 'POST':
        roll = request.form['roll']
        amount = float(request.form['amount'])
        mode = request.form['mode']
        remark = request.form.get('remark', '')

        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT id, name, phone, email FROM students WHERE roll_no=?", (roll,))
            stud = c.fetchone()
            if not stud:
                return "Roll number not found!"
            student_id = stud['id']
            student_name = stud['name']
            student_phone = stud['phone']
            student_email = stud['email']

            date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO fees (student_id, amount, date, mode, remark) VALUES (?, ?, ?, ?, ?)",
                      (student_id, amount, date, mode, remark))
            conn.commit()
            fee_id = c.lastrowid

            try:
                if student_phone:
                    send_sms(student_phone, f"Hi {student_name}! ₹{amount} fees received successfully.")
                if student_email:
                    send_email(student_email, "Fees Receipt", f"Hi {student_name}, Your fee of ₹{amount} has been received.")
            except Exception as e:
                print("Notification send error (fees):", e)

        return redirect(url_for('receipt', fee_id=fee_id))

    return render_template('fees.html')


@app.route('/fees-list')
@login_required
def fees_list():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT fees.id, students.name, students.roll_no, fees.amount, fees.date, fees.mode, fees.remark
            FROM fees JOIN students ON fees.student_id = students.id
            ORDER BY fees.id DESC
        """)
        rows = c.fetchall()
    return render_template('fees_list.html', data=rows)


# -------------------- Receipt (printable) & PDF ----------------------------
@app.route('/receipt/<int:fee_id>')
@login_required
def receipt(fee_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT fees.*, students.name, students.roll_no, students.course, students.phone
            FROM fees JOIN students ON fees.student_id = students.id
            WHERE fees.id=?
        """, (fee_id,))
        r = c.fetchone()
        if not r:
            return "Receipt not found"
    return render_template('receipt.html', r=r)


@app.route('/receipt/<int:fee_id>/pdf')
@login_required
def receipt_pdf(fee_id):
    if not REPORTLAB_AVAILABLE:
        return "PDF generation not available on server. Install reportlab or use print-to-PDF from browser."
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT fees.*, students.name, students.roll_no, students.course, students.phone
            FROM fees JOIN students ON fees.student_id = students.id
            WHERE fees.id=?
        """, (fee_id,))
        r = c.fetchone()
        if not r:
            return "Receipt not found"

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(50, 800, "Rajiv Gandhi Polytechnic - Fee Receipt")
    p.setFont("Helvetica", 12)
    p.drawString(50, 770, f"Name: {r['name']}")
    p.drawString(50, 750, f"Roll No: {r['roll_no']}")
    p.drawString(50, 730, f"Course: {r['course']}")
    p.drawString(50, 710, f"Amount: ₹{r['amount']}")
    p.drawString(50, 690, f"Date: {r['date']}")
    p.drawString(50, 670, f"Mode: {r['mode']}")
    p.drawString(50, 650, f"Remark: {r['remark']}")
    p.line(40, 640, 550, 640)
    p.drawString(50, 620, "Signature: ___________________")
    p.showPage()
    p.save()

    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"receipt_{fee_id}.pdf", mimetype='application/pdf')


# -------------------- Reports / Dues --------------------------------------
@app.route('/reports/fees-summary')
@login_required
def fees_summary():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees")
        total = c.fetchone()[0]
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE date LIKE ?", (today + "%",))
        today_total = c.fetchone()[0]
    return render_template('fees_summary.html', total=total, today_total=today_total)


@app.route('/reports/dues')
@login_required
def dues_report():
    q = request.args.get('q','').strip()
    with get_db() as conn:
        c = conn.cursor()
        if q:
            c.execute("SELECT * FROM students WHERE roll_no LIKE ? OR name LIKE ? ORDER BY id DESC", (f"%{q}%", f"%{q}%"))
        else:
            c.execute("SELECT * FROM students ORDER BY id DESC")
        students = c.fetchall()
        results = []
        for s in students:
            c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE student_id=?", (s['id'],))
            paid = c.fetchone()[0]
            due = (s['total_fee'] or 0) - paid
            results.append({'student': s, 'paid': paid, 'due': due})
    return render_template('dues_report.html', results=results, q=q)


# -------------------- Student profile & search ---------------------------
@app.route('/student/<int:student_id>')
@login_required
def student_profile(student_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM students WHERE id=?", (student_id,))
        s = c.fetchone()
        if not s:
            return "Student not found"
        c.execute("SELECT * FROM fees WHERE student_id=? ORDER BY id DESC", (student_id,))
        fees = c.fetchall()
        c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE student_id=?", (student_id,))
        paid = c.fetchone()[0]
        due = (s['total_fee'] or 0) - paid
    return render_template('student_profile.html', s=s, fees=fees, paid=paid, due=due)


@app.route('/search', methods=['GET','POST'])
@login_required
def search():
    result = None
    q = ''
    if request.method == 'POST':
        q = request.form['q'].strip()
        with get_db() as conn:
            c = conn.cursor()
            c.execute("SELECT * FROM students WHERE roll_no=? OR name LIKE ?", (q, f"%{q}%"))
            s = c.fetchone()
            if s:
                c.execute("SELECT * FROM fees WHERE student_id=? ORDER BY id DESC", (s['id'],))
                fees = c.fetchall()
                c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE student_id=?", (s['id'],))
                paid = c.fetchone()[0]
                due = (s['total_fee'] or 0) - paid
                result = {'s': s, 'fees': fees, 'paid': paid, 'due': due}
            else:
                result = None
    return render_template('search.html', result=result, q=q)


# -------------------- Export (CSV) ---------------------------------------
@app.route('/export/students')
@login_required
def export_students():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, roll_no, course, year, phone, total_fee FROM students ORDER BY id")
        rows = c.fetchall()

    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Roll No", "Course", "Year", "Phone", "Total Fee"])
    for s in rows:
        writer.writerow([s["id"], s["name"], s["roll_no"], s["course"], s["year"], s["phone"], s["total_fee"]])
    mem = output.getvalue().encode("utf-8")
    return send_file(BytesIO(mem), as_attachment=True, download_name="students.csv", mimetype="text/csv")


@app.route('/export/fees')
@login_required
def export_fees():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT fees.id, students.name, students.roll_no, fees.amount,
                   fees.date, fees.mode, fees.remark
            FROM fees
            JOIN students ON fees.student_id = students.id
            ORDER BY fees.id
        """)
        rows = c.fetchall()

    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Roll", "Amount", "Date", "Mode", "Remark"])
    for f in rows:
        writer.writerow([f["id"], f["name"], f["roll_no"], f["amount"], f["date"], f["mode"], f["remark"]])
    mem = output.getvalue().encode("utf-8")
    return send_file(BytesIO(mem), as_attachment=True, download_name="fees.csv", mimetype="text/csv")


@app.route('/export/dues')
@login_required
def export_dues():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, name, roll_no, course, phone, total_fee FROM students ORDER BY id")
        students = c.fetchall()

    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Roll", "Course", "Phone", "Total Fee", "Paid", "Due"])

    with get_db() as conn:
        c = conn.cursor()
        for s in students:
            c.execute("SELECT IFNULL(SUM(amount),0) FROM fees WHERE student_id=?", (s["id"],))
            paid = c.fetchone()[0]
            due = (s["total_fee"] or 0) - paid
            writer.writerow([s["id"], s["name"], s["roll_no"], s["course"], s["phone"], s["total_fee"], paid, due])

    mem = output.getvalue().encode("utf-8")
    return send_file(BytesIO(mem), as_attachment=True, download_name="dues.csv", mimetype="text/csv")


# -------------------- Run App ---------------------------------------------
if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.run(debug=True)
