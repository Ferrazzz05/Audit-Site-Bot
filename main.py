import os
import time
import smtplib
import logging
import requests
from typing import List, Dict, Any
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)

TARGET_URL = "https://editorafundamento.com.br/"
PAGES_TO_CHECK = [
    "/pages/monte-seu-box-pronto",
    "/products/monte-seu-box?page=addProductsPage1&currentFlow=byob",
    "/collections/box-pronto",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob",
    "/pages/mundo-alfabeto",
    "/pages/superpoderes-matematica",
]


def check_page(url: str) -> Dict[str, Any]:
    logging.info(f"Auditando: {url}")
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        start = time.time()
        req = requests.get(url, headers=headers, timeout=10)
        elapsed = round(time.time() - start, 2)
        return {"url": url, "status": req.status_code, "load_time_seconds": elapsed}
    except requests.RequestException as e:
        logging.error(f"Erro ao acessar {url}: {e}")
        return {"url": url, "status": f"Erro: {e}", "load_time_seconds": 0}


def run_audit() -> List[Dict[str, Any]]:
    results = []
    results.append(check_page(TARGET_URL))
    for path in PAGES_TO_CHECK:
        full_url = TARGET_URL.rstrip("/") + "/" + path.lstrip("/")
        results.append(check_page(full_url))
    return results


def send_email_report(results: List[Dict[str, Any]]) -> None:
    sender_email = os.environ.get("SMTP_USER", "")
    sender_password = os.environ.get("SMTP_PASS", "")
    receiver_email = os.environ.get("TO_EMAIL", "")

    if not sender_email or not sender_password or not receiver_email:
        logging.warning("Credenciais SMTP não configuradas — email não enviado.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Relatório de Auditoria: Editora Fundamento"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    total = len(results)
    ok = sum(1 for r in results if str(r.get("status")) == "200")
    status_geral = "Tudo OK." if ok == total else f"Atenção: {total - ok} página(s) com erro."

    text_body = f"Auditoria Editora Fundamento\n\n{status_geral}\n\n"
    html_body = f"<h2>Auditoria Editora Fundamento</h2><p>{status_geral}</p><ul>"

    for r in results:
        flag = "OK" if str(r.get("status")) == "200" else "ERRO"
        text_body += f"- {r['url']}: {flag} ({r['status']})\n"
        html_body += f"<li><strong>{r['url']}</strong> — {flag} ({r['status']})</li>"

    html_body += "</ul>"
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email.split(","), msg.as_string())
        server.quit()
        logging.info("Relatório enviado por email.")
    except Exception as e:
        logging.error(f"Falha ao enviar email: {e}")


if __name__ == "__main__":
    logging.info("Iniciando auditoria...")
    audit_results = run_audit()

    print("\nResultados:")
    for r in audit_results:
        flag = "OK  " if str(r.get("status")) == "200" else "ERRO"
        print(f"  [{flag}] {r['url']} — {r['status']}")

    total = len(audit_results)
    ok = sum(1 for r in audit_results if str(r.get("status")) == "200")
    print(f"\n  {ok}/{total} checks passaram\n")

    send_email_report(audit_results)
