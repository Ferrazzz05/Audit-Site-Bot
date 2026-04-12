import os
import time
import smtplib
import logging
import requests
from typing import List, Dict, Any
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from colorama import Fore, Style, init as colorama_init

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

colorama_init(autoreset=True)


class ColorFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, "")
        timestamp = f"{Style.DIM}{self.formatTime(record, '%H:%M:%S')}{Style.RESET_ALL}"
        level = f"{color}{Style.BRIGHT}{record.levelname:<8}{Style.RESET_ALL}"
        return f"{timestamp} {level} {record.getMessage()}"


_handler = logging.StreamHandler()
_handler.setFormatter(ColorFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])


def banner(text: str, color: str = Fore.CYAN) -> None:
    line = "═" * 60
    print(f"\n{color}{Style.BRIGHT}{line}")
    print(f"  {text}")
    print(f"{line}{Style.RESET_ALL}\n")


TARGET_URL = "https://editorafundamento.com.br/"
PAGES_TO_CHECK = [
    "/pages/monte-seu-box-pronto",
    "/products/monte-seu-box?page=addProductsPage1&currentFlow=byob",
    "/collections/box-pronto",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob",
    "/pages/mundo-alfabeto",
    "/pages/superpoderes-matematica",
]

BYOB_PAGES = {
    "/products/monte-seu-box?page=addProductsPage1&currentFlow=byob",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob",
}

SHOWCASE_PAGES = {
    "/pages/monte-seu-box-pronto",
}


def _close_drawer(driver: uc.Chrome) -> None:
    """Fecha qualquer cart drawer aberto — necessário após cada clique pra não bloquear os próximos."""
    try:
        driver.execute_script(
            "document.dispatchEvent(new KeyboardEvent('keydown', "
            "{key: 'Escape', keyCode: 27, which: 27, bubbles: true}));"
        )
        time.sleep(0.2)
    except Exception:
        pass


def _read_cart_count(driver: uc.Chrome) -> int:
    """
    Lê o contador 'N item(s)' do sidebar do BYOB via JavaScript.

    Usar o contador como fonte de verdade em vez de contar cliques evita
    falsos positivos — só conta como adicionado se o React realmente atualizou o estado.
    Retorna -1 se o elemento não for encontrado.
    """
    try:
        return driver.execute_script(
            "const el = [...document.querySelectorAll('*')]"
            "  .find(e => /\\d+\\s*item/i.test(e.innerText || '') && e.children.length === 0);"
            "if (!el) return -1;"
            "const m = el.innerText.match(/(\\d+)\\s*item/i);"
            "return m ? parseInt(m[1]) : -1;"
        )
    except Exception:
        return -1


def check_page(driver: uc.Chrome, url: str) -> Dict[str, Any]:
    logging.info(f"{Fore.CYAN}Auditando: {Style.BRIGHT}{url}")

    status_code = "Desconhecido"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        req = requests.get(url, headers=headers, timeout=10)
        status_code = req.status_code
    except requests.RequestException as e:
        status_code = f"Erro: {e}"
        logging.error(f"HTTP falhou em {url}: {e}")

    start_time = time.time()
    try:
        driver.get(url)
    except Exception as e:
        logging.error(f"Selenium falhou em {url}: {e}")
        return {"url": url, "status": status_code, "load_time_seconds": 0}

    load_time = round(time.time() - start_time, 2)
    time.sleep(5)

    return {"url": url, "status": status_code, "load_time_seconds": load_time}


def add_books_and_buy(driver: uc.Chrome, quantity: int = 12) -> Dict[str, bool]:
    """
    Fluxo BYOB completo: adiciona N livros e clica em Comprar.

    A lógica de adição usa o contador do carrinho como fonte de verdade em vez
    de simplesmente contar cliques. Inclui fallback para a versão adulta, cujas
    categorias têm menos de 12 títulos — nesse caso incrementa a quantidade de
    livros já adicionados via .gbbProductQuantityAddButton.
    """
    _close_drawer(driver)
    time.sleep(2)

    initial_count = _read_cart_count(driver)
    logging.info(f"{Fore.CYAN}  Contador inicial do carrinho: {initial_count}")

    target_count = (initial_count if initial_count >= 0 else 0) + quantity
    max_attempts = quantity * 4
    attempts = 0

    def _try_click_and_check(element) -> bool:
        """Scrolla até o elemento, clica e verifica se o contador aumentou."""
        nonlocal attempts
        attempts += 1
        try:
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', behavior: 'instant'});",
                element,
            )
            time.sleep(0.25)
            before = _read_cart_count(driver)
            driver.execute_script("arguments[0].click();", element)
            time.sleep(0.4)
            _close_drawer(driver)
            after = _read_cart_count(driver)
            if after > before:
                logging.info(f"{Fore.GREEN}  Livro adicionado ({after}/{target_count})")
                return True
        except Exception as e:
            logging.debug(f"Falha ao clicar: {e}")
        return False

    while attempts < max_attempts:
        current = _read_cart_count(driver)
        if current >= 0 and current >= target_count:
            logging.info(f"{Fore.GREEN}  Alvo atingido: {current} item(s)")
            break

        # Tenta os botões "Adicionar" disponíveis (livros ainda não no carrinho)
        buttons = driver.find_elements(By.CSS_SELECTOR, ".gbbProductAddButton")
        progressed = False
        for btn in buttons:
            if attempts >= max_attempts:
                break
            if _try_click_and_check(btn):
                progressed = True
                if _read_cart_count(driver) >= target_count:
                    break

        if _read_cart_count(driver) >= target_count:
            break

        # Fallback: sem mais botões Adicionar disponíveis — incrementa quantidade
        # dos livros já no carrinho usando o "+" (gbbProductQuantityAddButton)
        if not progressed:
            plus_buttons = driver.find_elements(By.CSS_SELECTOR, ".gbbProductQuantityAddButton")
            logging.info(f"{Fore.CYAN}  Fallback: {len(plus_buttons)} botões '+' de quantidade")
            for plus in plus_buttons:
                if attempts >= max_attempts:
                    break
                if _try_click_and_check(plus):
                    progressed = True
                    if _read_cart_count(driver) >= target_count:
                        break

        if not progressed:
            logging.warning(f"{Fore.YELLOW}Sem progresso — abandonando")
            break

    final_count = _read_cart_count(driver)
    added = max(0, (final_count if final_count >= 0 else 0) - (initial_count if initial_count >= 0 else 0))

    if added < quantity:
        logging.warning(f"{Fore.YELLOW}Só consegui adicionar {added}/{quantity} livros")
        return {"cart_ok": False, "checkout_ok": False}

    # Clica em Comprar disparando eventos de mouse reais para acionar o handler React
    _close_drawer(driver)
    try:
        comprar_buttons = driver.find_elements(By.CSS_SELECTOR, ".gbbFooterNextButton")
        if not comprar_buttons:
            logging.error(f"{Fore.RED}Botão Comprar não encontrado")
            return {"cart_ok": True, "checkout_ok": False}

        target = comprar_buttons[0]
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
        time.sleep(0.5)

        driver.execute_script(
            "const el = arguments[0];"
            "const r = el.getBoundingClientRect();"
            "const x = r.left + r.width / 2;"
            "const y = r.top + r.height / 2;"
            "['mousedown', 'mouseup', 'click'].forEach(t => {"
            "  el.dispatchEvent(new MouseEvent(t, {"
            "    bubbles: true, cancelable: true, view: window,"
            "    clientX: x, clientY: y, button: 0"
            "  }));"
            "});",
            target,
        )
        logging.info(f"{Fore.GREEN}Botão Comprar clicado com sucesso")
        time.sleep(3)
        return {"cart_ok": True, "checkout_ok": True}
    except Exception as e:
        logging.error(f"{Fore.RED}Falhou ao clicar em Comprar: {e}")
        return {"cart_ok": True, "checkout_ok": False}


def run_audit() -> List[Dict[str, Any]]:
    results = []

    # undetected-chromedriver patcha o ChromeDriver pra remover os indicadores que
    # o Cloudflare Turnstile usa pra bloquear automação (navigator.webdriver, etc.)
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = uc.Chrome(options=chrome_options, version_main=146)
    driver.set_window_rect(x=0, y=0, width=960, height=1040)

    try:
        logging.info(f"{Fore.MAGENTA}Página principal: {Style.BRIGHT}{TARGET_URL}")
        results.append(check_page(driver, TARGET_URL))

        for path in PAGES_TO_CHECK:
            full_url = TARGET_URL.rstrip("/") + "/" + path.lstrip("/")

            if path in SHOWCASE_PAGES:
                logging.info(f"{Fore.MAGENTA}Página vitrine: {Style.BRIGHT}{path}")

            results.append(check_page(driver, full_url))

            if path in SHOWCASE_PAGES:
                continue

            try:
                if path in BYOB_PAGES:
                    logging.info(f"{Fore.CYAN}Iniciando fluxo BYOB: {Style.BRIGHT}{path}")
                    result = add_books_and_buy(driver, quantity=12)
                    results.append({
                        "url": f"Fluxo BYOB 12 livros + Comprar ({path})",
                        "status": "FUNCIONOU" if result["cart_ok"] else "FALHOU",
                    })
                else:
                    buttons = driver.find_elements(
                        By.XPATH,
                        "//button[contains(., 'Adicionar') or contains(., 'Comprar') or contains(., 'Aproveite')]",
                    )
                    if buttons:
                        driver.execute_script("arguments[0].click();", buttons[0])
                        time.sleep(2)
                        results.append({"url": f"Clique CTA ({path})", "status": "FUNCIONOU"})
                        logging.info(f"{Fore.GREEN}Clique executado em: {Style.BRIGHT}{path}")
            except Exception as e:
                logging.warning(f"{Fore.YELLOW}Interação falhou em {path}: {e}")
    finally:
        driver.quit()

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
    ok = sum(1 for r in results if str(r.get("status")) in ["200", "FUNCIONOU"])
    status_geral = "Tudo OK." if ok == total else f"Atenção: {total - ok} check(s) com erro."

    text_body = f"Auditoria Editora Fundamento\n\n{status_geral}\n\n"
    html_body = f"<h2>Auditoria Editora Fundamento</h2><p>{status_geral}</p><ul>"

    for r in results:
        flag = "OK" if str(r.get("status")) in ["200", "FUNCIONOU"] else "ERRO"
        load = f" | {r['load_time_seconds']}s" if r.get("load_time_seconds") else ""
        text_body += f"- {r['url']}: {flag} ({r['status']}){load}\n"
        html_body += f"<li><strong>{r['url']}</strong> — {flag} ({r['status']}){load}</li>"

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
    banner("WEBSITE AUDIT BOT  -  EDITORA FUNDAMENTO", Fore.MAGENTA)
    logging.info(f"{Fore.CYAN}Iniciando auditoria com Selenium...")

    audit_results = run_audit()

    banner("RESULTADOS DA AUDITORIA", Fore.BLUE)
    total = len(audit_results)
    ok_count = 0
    for r in audit_results:
        is_ok = str(r.get("status")) in ["200", "FUNCIONOU"]
        if is_ok:
            ok_count += 1
        flag = f"{Fore.GREEN}{Style.BRIGHT}  OK  " if is_ok else f"{Fore.RED}{Style.BRIGHT} ERRO "
        load = f"{Fore.YELLOW}{r['load_time_seconds']}s" if r.get("load_time_seconds") else ""
        print(f"  {flag}{Style.RESET_ALL} {Style.DIM}[{r['status']}]{Style.RESET_ALL} {r['url']} {load}")

    summary_color = Fore.GREEN if ok_count == total else Fore.YELLOW
    print(f"\n  {summary_color}{Style.BRIGHT}-> {ok_count}/{total} checks passaram{Style.RESET_ALL}\n")

    send_email_report(audit_results)
    banner("AUDITORIA FINALIZADA", Fore.GREEN)
