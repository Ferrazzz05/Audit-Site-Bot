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


# URL base do site monitorado
TARGET_URL = "https://editorafundamento.com.br/"

# Todas as páginas que entram na auditoria — ordem importa pra navegação sequencial
PAGES_TO_CHECK = [
    "/pages/monte-seu-box-pronto",
    "/pages/box-personalizado-novo",
    "/collections/box-pronto",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob",
    "/pages/mundo-alfabeto",
    "/pages/superpoderes-matematica",
]

# Páginas que exigem o fluxo completo: adicionar 12 livros + clicar Comprar + validar checkout
# Cada página tem o seletor CSS do botão "Adicionar" descoberto via DevTools
BYOB_PAGES = {
    "/pages/box-personalizado-novo",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob",
}

BYOB_SELECTORS = {
    "/pages/box-personalizado-novo": "button[class*='variantSelector_container']",
    "/products/monte-seu-box-adulto?page=addProductsPage1&currentFlow=byob": ".gbbProductAddButton",
}

# Páginas vitrine: auditadas apenas por status e tempo de carga, sem interação
SHOWCASE_PAGES = {
    "/pages/monte-seu-box-pronto",
}


def _close_drawer(driver: uc.Chrome) -> None:
    """Fecha qualquer cart drawer aberto via Escape — evita que sobreponha os botões nos passos seguintes."""
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
    Lê o contador 'N item(s)' do sidebar BYOB diretamente no DOM via JavaScript.

    Usar o contador como fonte de verdade em vez de contar cliques elimina falsos
    positivos — só registra livro adicionado quando o React realmente atualizou o estado.
    Retorna -1 se o elemento não for encontrado na página.
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


def _verify_checkout_reached(driver: uc.Chrome, timeout: float = 20.0) -> bool:
    """
    Valida que o clique em Comprar realmente navegou para o checkout.

    Só aceita URLs com /checkout ou checkout.shopify — evita falsos positivos
    quando o clique redireciona para outra página (ex: página inicial).
    Timeout de 20s porque o Shopify leva ~11s pra redirecionar dependendo da carga.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = driver.current_url or ""
        if any(k in url.lower() for k in ("/checkout", "checkout.shopify")):
            return True
        time.sleep(0.3)
    return False


def _cart_to_checkout(driver: uc.Chrome, timeout: float = 15.0) -> bool:
    """
    Após um clique de compra, aguarda o caminho para o checkout.
    Suporta dois fluxos:
    - Navegação para /cart (redireciona a página inteira)
    - Drawer de carrinho (a URL não muda, mas o botão de checkout aparece no DOM)
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "/cart" in (driver.current_url or "").lower():
            break
        if driver.find_elements(By.CSS_SELECTOR, "[name='checkout']"):
            break
        time.sleep(0.3)
    else:
        return False

    checkout_btn = driver.find_elements(By.CSS_SELECTOR, "[name='checkout']")
    if not checkout_btn:
        checkout_btn = driver.find_elements(
            By.XPATH,
            "//button[contains(., 'Finalizar') or contains(., 'Confira') or contains(., 'Checkout')]",
        )
    if not checkout_btn:
        return False

    driver.execute_script("arguments[0].click();", checkout_btn[0])
    return _verify_checkout_reached(driver, timeout=20.0)


def check_page(driver: uc.Chrome, url: str) -> Dict[str, Any]:
    """
    Audita uma página verificando status HTTP via requests e tempo de carga real via Selenium.

    Usa dois métodos complementares: requests é rápido e retorna o status code HTTP,
    Selenium mede o tempo de renderização no browser como um usuário real veria.
    """
    logging.info(f"{Fore.CYAN}Auditando página: {Style.BRIGHT}{url}")

    status_code = "Desconhecido"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = requests.get(url, headers=headers, timeout=10)
        status_code = req.status_code
    except requests.RequestException as e:
        status_code = f"Erro de Conexão: {e}"
        logging.error(f"Failed to fetch {url} via HTTP: {e}")

    start_time = time.time()
    try:
        driver.get(url)
    except Exception as e:
        logging.error(f"Failed to load page with Selenium: {url} - {e}")
        return {"url": url, "status": status_code, "load_time_seconds": 0}

    load_time = time.time() - start_time

    # Aguarda hidratação do React antes de procurar botões
    time.sleep(5)

    return {"url": url, "status": status_code, "load_time_seconds": round(load_time, 2)}


def add_books_and_buy(driver: uc.Chrome, quantity: int = 12, add_selector: str = ".gbbProductAddButton") -> Dict[str, bool]:
    """
    Fluxo BYOB completo: adiciona N livros ao carrinho e clica em Comprar.

    Decisões de implementação relevantes:
    - Os botões do fluxo adulto são <div class="gbbProductAddButton">, não <button>.
      O texto "Adicionar" vem de um ::after CSS, então XPath por texto visível falha.
    - Para a nova página infantil, o contador DOM retorna -1; nesse caso conta os cliques.
    - O clique em Comprar usa dispatchEvent com MouseEvent real (mousedown + mouseup + click)
      porque o element.click() do Selenium nem sempre aciona Synthetic Events do React.
    - Fallback para .gbbProductQuantityAddButton quando a categoria não tem 12 títulos
      (acontece na versão adulta) — incrementa a quantidade de um livro já adicionado.
    """
    _close_drawer(driver)
    time.sleep(2)

    initial_count = _read_cart_count(driver)
    logging.info(f"{Fore.CYAN}  Contador inicial do carrinho: {initial_count}")

    target_count = (initial_count if initial_count >= 0 else 0) + quantity
    added = 0  # contador de cliques — usado quando _read_cart_count não está disponível

    def _try_click_and_check(element) -> bool:
        """Scrolla até o elemento, clica e confirma se o livro foi adicionado.
        Usa o contador do DOM quando disponível; caso contrário confia no clique."""
        nonlocal added
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
                added += 1
                logging.info(f"{Fore.GREEN}  Livro adicionado ({added}/{quantity})")
                return True
            if before == -1:
                # Contador indisponível nessa página — trata o clique como bem-sucedido
                added += 1
                logging.info(f"{Fore.GREEN}  Livro adicionado ({added}/{quantity})")
                return True
        except Exception as e:
            logging.debug(f"Falha ao clicar: {e}")
        return False

    while added < quantity:
        current = _read_cart_count(driver)
        if current >= 0 and current >= target_count:
            logging.info(f"{Fore.GREEN}  Alvo atingido: {current} item(s)")
            break

        # 1ª tentativa: clica nos botões Adicionar de livros ainda não no carrinho
        buttons = driver.find_elements(By.CSS_SELECTOR, add_selector)
        progressed = False
        for btn in buttons:
            if added >= quantity:
                break
            if _try_click_and_check(btn):
                progressed = True
                if added >= quantity or _read_cart_count(driver) >= target_count:
                    break

        if added >= quantity or _read_cart_count(driver) >= target_count:
            break

        # 2ª tentativa (fallback): quando não há mais botões Adicionar disponíveis,
        # incrementa a quantidade de livros já no carrinho via botão "+"
        if not progressed:
            plus_buttons = driver.find_elements(By.CSS_SELECTOR, ".gbbProductQuantityAddButton")
            logging.info(f"{Fore.CYAN}  Fallback: {len(plus_buttons)} botões '+' de quantidade")
            for plus in plus_buttons:
                if added >= quantity:
                    break
                if _try_click_and_check(plus):
                    progressed = True
                    if _read_cart_count(driver) >= target_count:
                        break

        if not progressed:
            logging.warning(f"{Fore.YELLOW}Sem progresso — abandonando")
            break

    # Usa o contador DOM como fonte de verdade quando disponível;
    # senão mantém o added acumulado pelo loop (fallback para páginas sem contador)
    final_count = _read_cart_count(driver)
    if final_count >= 0:
        added = max(0, final_count - (initial_count if initial_count >= 0 else 0))

    if added < quantity:
        logging.warning(f"{Fore.YELLOW}Só consegui adicionar {added}/{quantity} livros")
        return {"cart_ok": False, "checkout_ok": False}

    # Clica em Comprar usando eventos de mouse reais para garantir que o React processe
    _close_drawer(driver)
    try:
        # Tenta os seletores conhecidos de cada fluxo antes de recorrer ao XPath por texto
        comprar_buttons = driver.find_elements(By.CSS_SELECTOR, ".gbbFooterNextButton")
        if not comprar_buttons:
            comprar_buttons = driver.find_elements(By.CSS_SELECTOR, ".rbr-addBundleBtn-container")
        if not comprar_buttons:
            comprar_buttons = driver.find_elements(
                By.XPATH,
                "//button[contains(., 'Comprar') or contains(., 'Finalizar')]",
            )
        target = comprar_buttons[0] if comprar_buttons else None

        if target is None:
            logging.error(f"{Fore.RED}Botão 'Comprar' não encontrado")
            return {"cart_ok": True, "checkout_ok": False}

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
        logging.info(f"{Fore.GREEN}Botão 'Comprar' clicado com sucesso")

        if _verify_checkout_reached(driver, timeout=20.0):
            logging.info(f"{Fore.GREEN}Checkout alcançado: {driver.current_url}")
            return {"cart_ok": True, "checkout_ok": True}
        else:
            logging.warning(
                f"{Fore.YELLOW}Clique em 'Comprar' não levou ao checkout "
                f"(URL atual: {driver.current_url})"
            )
            return {"cart_ok": True, "checkout_ok": False}
    except Exception as e:
        logging.error(f"{Fore.RED}Falhou ao clicar em 'Comprar': {e}")
        return {"cart_ok": True, "checkout_ok": False}


def run_audit() -> List[Dict[str, Any]]:
    """
    Orquestra a auditoria completa: visita todas as páginas, roda o fluxo BYOB
    nas páginas de conversão e retorna a lista de resultados consolidada.
    """
    results = []

    # undetected-chromedriver patcha o ChromeDriver removendo os indicadores que
    # o Cloudflare Turnstile usa pra bloquear automação (navigator.webdriver, etc.)
    chrome_options = uc.ChromeOptions()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    try:
        driver = uc.Chrome(options=chrome_options, version_main=146)
    except Exception:
        driver = uc.Chrome(options=chrome_options)
    # Metade esquerda da tela em FullHD — deixa o terminal visível na metade direita
    driver.set_window_rect(x=0, y=0, width=960, height=1040)

    try:
        logging.info(f"{Fore.MAGENTA}Página principal: {Style.BRIGHT}{TARGET_URL}")
        results.append(check_page(driver, TARGET_URL))

        for path in PAGES_TO_CHECK:
            full_url = TARGET_URL.rstrip("/") + "/" + path.lstrip("/")

            if path in SHOWCASE_PAGES:
                logging.info(f"{Fore.MAGENTA}Página vitrine: {Style.BRIGHT}{path}")

            res = check_page(driver, full_url)
            results.append(res)

            if path in SHOWCASE_PAGES:
                # Página vitrine: apenas auditoria de status e tempo, sem interação
                continue

            try:
                if path in BYOB_PAGES:
                    logging.info(f"{Fore.CYAN}Iniciando fluxo BYOB em: {Style.BRIGHT}{path}")
                    selector = BYOB_SELECTORS.get(path, ".gbbProductAddButton")
                    result = add_books_and_buy(driver, quantity=12, add_selector=selector)
                    results.append({
                        "url": f"Fluxo BYOB 12 livros + Comprar ({path})",
                        "status": "FUNCIONOU" if result["cart_ok"] else "FALHOU",
                    })
                    results.append({
                        "url": f"Checkout alcançado ({path})",
                        "status": "FUNCIONOU" if result["checkout_ok"] else "FALHOU",
                    })
                else:
                    # Páginas de conversão simples: clica no primeiro CTA disponível
                    buttons = driver.find_elements(
                        By.XPATH,
                        "//button[contains(., 'Adicionar') or contains(., 'Comprar') "
                        "or contains(., 'Compre') or contains(., 'Aproveite') or contains(., 'Avançar')]",
                    )
                    if buttons:
                        driver.execute_script("arguments[0].click();", buttons[0])
                        checkout_ok = _cart_to_checkout(driver)
                        status = "FUNCIONOU" if checkout_ok else "FALHOU"
                        results.append({"url": f"Checkout ({path})", "status": status})
                        if checkout_ok:
                            logging.info(f"{Fore.GREEN}Checkout validado em: {Style.BRIGHT}{path}")
                        else:
                            logging.warning(f"{Fore.YELLOW}Checkout não alcançado em: {path}")
                    else:
                        logging.debug(f"Nenhum botão de compra encontrado em: {path}")
            except Exception as e:
                logging.warning(f"{Fore.YELLOW}Não foi possível interagir com {path}: {e}")
    finally:
        driver.quit()

    return results


def send_email_report(results: List[Dict[str, Any]]) -> None:
    """
    Gera e envia o relatório consolidado por email via SMTP.
    As credenciais são lidas de variáveis de ambiente para não ficarem no código.
    """
    sender_email = os.environ.get("SMTP_USER", "")
    sender_password = os.environ.get("SMTP_PASS", "")
    receiver_email = os.environ.get("TO_EMAIL", "")

    if not sender_email or not sender_password or not receiver_email:
        logging.warning("Skipping email... SMTP credentials or TO_EMAIL not set in environment.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Relatório de Auditoria: Editora Fundamento"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    total_pages = len(results)
    pages_ok = sum(1 for r in results if str(r.get("status")) in ["200", "FUNCIONOU"])

    status_geral = (
        "✅ Tudo funcionando perfeitamente (Páginas e Carrinho)."
        if pages_ok == total_pages
        else "⚠️ Atenção: Alguma página ou teste falhou."
    )

    html_body = f"""
    <h2>Auditoria Editora Fundamento</h2>
    <p><strong>Status Geral:</strong> {status_geral}</p>
    <hr>
    <ul>
    """
    text_body = f"Relatório de Auditoria: Editora Fundamento\n\nStatus Geral: {status_geral}\n\nResumo:\n"

    for r in results:
        status_val = str(r.get("status"))
        is_ok = status_val in ["200", "FUNCIONOU"]
        status_flag = "✅ OK" if is_ok else "❌ ERRO"
        load_time_txt = (
            f" | ⏳ {r['load_time_seconds']}s"
            if "load_time_seconds" in r and r["load_time_seconds"] > 0
            else ""
        )
        text_body += f"- {r['url']}: {status_flag} ({status_val}){load_time_txt}\n"
        html_body += f"""
        <li>
            <strong>{r['url']}</strong> - {status_flag} ({status_val}){load_time_txt}
        </li>
        """

    html_body += "</ul>"

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        logging.info("Connecting to SMTP server...")
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, sender_password)
        to_addrs = [email.strip() for email in receiver_email.split(",") if email.strip()]
        server.sendmail(sender_email, to_addrs, msg.as_string())
        server.quit()
        logging.info("Email report sent successfully!")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


if __name__ == "__main__":
    banner("WEBSITE AUDIT BOT  -  EDITORA FUNDAMENTO", Fore.MAGENTA)
    logging.info(f"{Fore.CYAN}Iniciando auditoria com Selenium...")

    audit_results = run_audit()

    banner("RESULTADOS DA AUDITORIA", Fore.BLUE)
    total = len(audit_results)
    ok_count = 0
    for r in audit_results:
        status_code = r.get("status")
        is_ok = str(status_code) in ["200", "FUNCIONOU"]
        if is_ok:
            ok_count += 1
        flag = f"{Fore.GREEN}{Style.BRIGHT}  OK  " if is_ok else f"{Fore.RED}{Style.BRIGHT} ERRO "
        load_time = r.get("load_time_seconds", "N/A")
        time_txt = f"{Fore.YELLOW}{load_time}s" if load_time and load_time != "N/A" else ""
        print(f"  {flag}{Style.RESET_ALL} {Style.DIM}[{status_code}]{Style.RESET_ALL} {r['url']} {time_txt}")

    summary_color = Fore.GREEN if ok_count == total else Fore.YELLOW
    print(f"\n  {summary_color}{Style.BRIGHT}-> {ok_count}/{total} checks passaram{Style.RESET_ALL}\n")

    logging.info(f"{Fore.CYAN}Gerando e enviando relatório por email...")
    send_email_report(audit_results)
    banner("AUDITORIA FINALIZADA", Fore.GREEN)
