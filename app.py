from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, stream_with_context, Response
import os
from dotenv import load_dotenv
from datetime import datetime
import sqlite3
from google import genai
import logging
import requests

# --- Configure Environment ---
load_dotenv()

# --- Configure Logging ---
logging.basicConfig(level=logging.DEBUG)

# --- Configure Gemini ---
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "default_secret_key")
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")

# --- SQLite Database Setup ---
DATABASE = 'finance_tracker.db'

def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,
            description TEXT,
            payment_method TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route("/chatbot", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")
    logging.debug(f"User message received: {user_message}")
    
    def generate():
        # --- Add Context for Smart Advice ---
        user_id = session.get('user_id')
        context = ""
        if user_id:
            conn = sqlite3.connect(DATABASE)
            c = conn.cursor()
            # Get latest 30 transactions for context
            c.execute("SELECT amount, category, date, description FROM transactions WHERE user_id = ? ORDER BY date DESC LIMIT 30", (user_id,))
            rows = c.fetchall()
            conn.close()
            
            if rows:
                context = "\nRecent transactions for context:\n"
                for row in rows:
                    context += f"- ₹{row[0]} for {row[1]} on {row[2]} ({row[3] or 'No details'})\n"
        
        # Build the final prompt with a system instruction
        system_instruction = f"You are a helpful Financial Advisor for this Finance Tracker app. Below is a summary of the user's recent spending. Use this to provide personalized advice if asked. Keep responses concise and friendly."
        full_message = f"{system_instruction}\n{context}\nUser: {user_message}"

        try:
            response_stream = client.models.generate_content_stream(
                model='gemini-flash-latest',
                contents=full_message
            )
            for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logging.error(f"Chatbot streaming error: {str(e)}")
            yield "Sorry, there was an issue with the chatbot. Please try again later."

    return Response(stream_with_context(generate()), mimetype='text/plain')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        is_localhost = request.host.split(':')[0] in ['localhost', '127.0.0.1']
        
        if not is_localhost:
            recaptcha_response = request.form.get('g-recaptcha-response')
            payload = {
                'secret': RECAPTCHA_SECRET_KEY,
                'response': recaptcha_response
            }
            try:
                r = requests.post('https://www.google.com/recaptcha/api/siteverify', data=payload)
                result = r.json()
                if not result.get('success'):
                    flash('Invalid CAPTCHA. Please try again.', 'danger')
                    return redirect(url_for('login'))
            except Exception as e:
                logging.error(f"ReCaptcha error: {str(e)}")
                # Optionally handle connection errors here
        
        username = request.form['username']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT id, username FROM users WHERE username = ? AND password = ?", (username, password))
        user = c.fetchone()
        conn.close()
        if user:
            session['user_id'] = user[0]
            session['username'] = user[1]
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password. Please try again.', 'error')
    is_localhost = request.host.split(':')[0] in ['localhost', '127.0.0.1']
    return render_template('login.html', skip_captcha=is_localhost)

@app.route('/')
def index():
    if 'username' in session:
        user_id = session['user_id']
        username = session['username']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,))
        transactions = c.fetchall()
        total_amount = sum(transaction[2] for transaction in transactions)
        total_upi = sum(transaction[2] for transaction in transactions if transaction[6] == 'UPI')
        total_cash = sum(transaction[2] for transaction in transactions if transaction[6] == 'Cash')
        conn.close()
        return render_template('index.html', username=username, total_amount=total_amount, total_upi=total_upi, total_cash=total_cash)
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        phone = request.form['phone']
        password = request.form['password']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        if c.fetchone():
            flash('Username already exists. Please choose a different one.', 'error')
        else:
            c.execute("INSERT INTO users (username, email, phone, password) VALUES (?, ?, ?, ?)",
                      (username, email, phone, password))
            conn.commit()
            flash('Registration successful! Please log in.', 'success')
        conn.close()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/transactions')
def transactions():
    if 'username' in session:
        user_id = session['user_id']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE user_id = ?", (user_id,))
        transactions = c.fetchall()
        conn.close()
        return render_template('transaction.html', transactions=transactions, username=session['username'])
    return redirect(url_for('login'))

@app.route('/add_transaction', methods=['POST'])
def add_transaction():
    if 'username' in session:
        user_id = session['user_id']
        date = request.form['date']
        category = request.form['category']
        amount = float(request.form['amount'])
        payment_method = request.form['payment_method']
        description = request.form['notes']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute('''
            INSERT INTO transactions (user_id, amount, category, date, description, payment_method)
            VALUES (?, ?, ?, ?, ?, ?)''', (user_id, amount, category, date, description, payment_method))
        conn.commit()
        conn.close()
        return redirect(url_for('transactions'))
    return redirect(url_for('login'))

@app.route('/delete_transaction/<int:transaction_id>', methods=['POST'])
def delete_transaction(transaction_id):
    if 'username' in session:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        conn.commit()
        conn.close()
        flash('Transaction deleted successfully.', 'success')
    else:
        flash('You must be logged in to delete a transaction.', 'error')
    return redirect(url_for('transactions'))

@app.route('/daily_spending_data')
def daily_spending_data():
    if 'username' in session:
        user_id = session['user_id']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT date, SUM(amount) FROM transactions WHERE user_id = ? GROUP BY date", (user_id,))
        data = c.fetchall()
        conn.close()
        labels = [row[0] for row in data]
        amounts = [row[1] for row in data]
        return jsonify({'labels': labels, 'amounts': amounts})
    return redirect(url_for('login'))

@app.route('/monthly_spending_data')
def monthly_spending_data():
    if 'username' in session:
        user_id = session['user_id']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT strftime('%Y-%m', date), SUM(amount) FROM transactions WHERE user_id = ? GROUP BY strftime('%Y-%m', date)", (user_id,))
        data = c.fetchall()
        conn.close()
        labels = [datetime.strptime(row[0], '%Y-%m').strftime('%b %Y') for row in data]
        amounts = [row[1] for row in data]
        return jsonify({'labels': labels, 'amounts': amounts})
    return redirect(url_for('login'))

@app.route('/category_spending_data')
def category_spending_data():
    if 'username' in session:
        user_id = session['user_id']
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT category, SUM(amount) FROM transactions WHERE user_id = ? GROUP BY category", (user_id,))
        data = c.fetchall()
        conn.close()
        labels = [row[0] for row in data]
        amounts = [row[1] for row in data]
        return jsonify({'labels': labels, 'amounts': amounts})
    return redirect(url_for('login'))

@app.route('/statistics')
def statistics():
    user_id = session.get('user_id')
    if user_id:
        conn = sqlite3.connect(DATABASE)
        c = conn.cursor()
        c.execute("SELECT SUM(amount) FROM transactions WHERE user_id = ?", (user_id,))
        total_expenses = c.fetchone()[0] or 0

        c.execute("SELECT category, SUM(amount) FROM transactions WHERE user_id = ? GROUP BY category", (user_id,))
        expense_by_category = dict(c.fetchall())

        c.execute("SELECT category, SUM(amount) FROM transactions WHERE user_id = ? GROUP BY category ORDER BY SUM(amount) DESC LIMIT 5", (user_id,))
        top_spending_categories = dict(c.fetchall())

        c.execute("SELECT amount, date FROM transactions WHERE user_id = ?", (user_id,))
        transactions = c.fetchall()

        today = datetime.today()
        current_month = today.month
        current_year = today.year

        previous_month = current_month - 1 if current_month > 1 else 12
        previous_year = current_year if current_month > 1 else current_year - 1

        current_month_total = 0
        previous_month_total = 0

        for amount, date_str in transactions:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                if date_obj.year == current_year and date_obj.month == current_month:
                    current_month_total += amount
                elif date_obj.year == previous_year and date_obj.month == previous_month:
                    previous_month_total += amount
            except Exception as e:
                logging.error(f"Date parsing error: {e}")
                continue

        difference = current_month_total - previous_month_total
        percentage_change = round((difference / previous_month_total * 100), 2) if previous_month_total != 0 else 0

        conn.close()

        return render_template(
            'statistics.html',
            total_expenses=round(total_expenses, 2),
            expense_by_category=expense_by_category,
        )

if __name__ == '__main__':
    app.run(debug=True)