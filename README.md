# Website Audit Bot - Editora Fundamento

Esse projeto nasceu de uma dor bem específica: garantir que páginas críticas de conversão de um e-commerce não quebrem silenciosamente. Uma página de produto fora do ar, um botão de compra que não responde, um checkout que não carrega — qualquer um desses problemas custa venda direta, e geralmente a gente só descobre quando um cliente reclama.

Então construí um bot em Python que roda periodicamente auditando as páginas mais importantes da loja, simula as interações que um usuário real faria (incluindo adicionar 12 livros no flow de "Monte seu Box" e avançar pro checkout) e dispara um relatório por email com o resultado. Se algo estiver errado, o alerta chega antes do cliente.

## O que o bot faz

- **Auditoria de status HTTP e tempo de carga** em cada página monitorada, usando `requests` pra verificar o headers/status rápido e `Selenium` pra medir o tempo real de renderização no navegador.
- **Fluxo completo BYOB (Build Your Own Box)** nas páginas de "Monte seu Box" infantil e adulto: o bot entra na página, adiciona 12 livros (alternando entre diferentes livros ou incrementando a quantidade de um já adicionado quando necessário), clica em "Comprar" e valida que a navegação avançou pro próximo passo.
- **Validação de checkout end-to-end**: depois do clique em "Comprar", verifica se a URL realmente mudou. Isso garante que a gente não tenha um falso positivo de "clique funcionou" quando na verdade nada aconteceu no fluxo.
- **Interações simuladas nas páginas de produto** (coleções, landing pages, carrinho pronto): procura e aciona os botões de CTA padrão como "Adicionar", "Comprar" e "Aproveite" pra confirmar que os handlers JavaScript estão respondendo.
- **Relatório consolidado** em HTML e texto disparado por SMTP, com status visual (OK/ERRO), tempo de carga de cada página e contagem total de checks que passaram.
- **Logs coloridos no terminal** pra facilitar o debug durante desenvolvimento, com banners de seção e destaque pro que é página vitrine (só auditada) versus página de conversão (com interação completa).

## Decisões técnicas que valem explicar

**Cloudflare bypass com undetected-chromedriver.** O site usa Cloudflare Turnstile, que detecta o Selenium normal pelos flags `navigator.webdriver` e a assinatura do ChromeDriver. Troquei o `webdriver.Chrome` por `undetected_chromedriver`, que patcheia o driver pra remover essas pistas e se passar por um Chrome humano. Sem isso, o bot era bloqueado logo na primeira requisição.

**Seletores CSS descobertos via DevTools.** As páginas de "Monte seu Box" usam uma estrutura React customizada com classes atômicas estilo Meta (`gbbProductAddButton`, `gbbProductQuantityAddButton`, `gbbFooterNextButton`, etc). Esses elementos não são `<button>` — são `<div>` com handlers React anexados, e o texto "Adicionar" vem de um `::after` CSS pseudo-element. Isso significa que qualquer XPath baseado em texto visível falha. A solução foi inspecionar manualmente o DOM, extrair os seletores CSS reais e usar esses diretamente via `find_elements(By.CSS_SELECTOR, ...)`.

**Eventos de mouse reais para acionar handlers React.** O método `element.click()` do Selenium (e o equivalente via `execute_script("arguments[0].click()")`) nem sempre dispara handlers React corretamente, porque React usa seu próprio sistema de Synthetic Events que valida se o evento é "trusted". Pra contornar isso, o clique no botão de Comprar é feito disparando `mousedown`, `mouseup` e `click` como `MouseEvent` completos via `dispatchEvent`, com coordenadas calculadas a partir do centro do elemento.

**Contador do carrinho como fonte de verdade.** No fluxo BYOB, em vez de contar quantas vezes o bot clicou em "Adicionar" (que pode dar falso positivo se o clique não processar), a cada iteração o bot lê o contador de itens do sidebar (`"N item(s)"`) via JavaScript e compara com o valor anterior. Só conta como livro adicionado se o contador realmente aumentou. Isso transforma o teste em algo auditável de verdade.

**Fallback pra categorias com menos de 12 livros.** Na versão adulta do "Monte seu Box", a primeira categoria só tem 11 livros. Em vez de trocar de aba (que dá margem pra inconsistências), o bot detecta quando esgotou os botões de "Adicionar" disponíveis e cai num fallback: procura os botões `.gbbProductQuantityAddButton` (o "+" que aparece em cada card já adicionado, junto com um contador "- 1 +") e incrementa a quantidade de um livro já no carrinho até completar os 12.

## Stack

- **Python 3** como linguagem principal
- **Selenium WebDriver** pra automação de browser, com `undetected-chromedriver` pra contornar detecção de bot
- **Requests** pra auditoria rápida de status HTTP
- **Colorama** pra logs coloridos no terminal
- **Python-dotenv** pra carregar credenciais SMTP do arquivo `.env`
- **GitHub Actions** como orquestrador de agendamento (cron + manual)

## Como rodar localmente

Instale as dependências:

```bash
pip install -r requirements.txt
```

Crie um arquivo `.env` na raiz com as credenciais SMTP (o `.gitignore` garante que esse arquivo não vai pro repositório):

```env
SMTP_USER=seu_email@gmail.com
SMTP_PASS=sua_senha_de_app
TO_EMAIL=destino@dominio.com
```

Pra o Gmail, você precisa gerar uma "senha de app" nas configurações de segurança da conta — a senha normal da conta não funciona com SMTP por segurança.

Depois é só executar:

```bash
python main.py
```

Você vai ver o Chrome abrir, navegar pelas páginas, adicionar livros aos boxes e clicar nos botões de compra. No final, um relatório formatado é impresso no terminal e disparado por email.

## Execução agendada

Além de rodar localmente, o projeto também é executado de forma agendada toda terça e sexta de manhã num ambiente privado via GitHub Actions. A ideia é que o bot rode sem intervenção humana no ciclo normal, e só exija atenção quando um relatório mostrar falha em alguma página crítica — transformando o monitoramento de "lembrar de verificar" em "só agir quando chegar alerta".

Este repositório contém apenas o código do bot; o workflow de automação fica num outro ambiente por questão de separação entre o que é portfolio público e o que é operação real.
