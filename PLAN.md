# Tradewars

## Introduction

This project will be a battle arena for 4 LLM Agents (the "traders") to compete in a simulated equity trading environment, day trading with a 1 hour time limit. The project will be developed by Claude Code.

## Rules

During the 1 hour of the gane, the traders can buy/sell equities. They cannot short sell.
The traders each start with $1,000,000 and they can trade in fractional shares. There's no commission or bid/offer spread.

## Traders

The traders will be developed using OpenAI Agents SDK.
The traders will have access to the Massive MCP Server (formerly known as Polygon.io). This will give them access to realtime and historic equity prices, news information, and technical and fundamental analysis. They will have access to a Playwright MCP Server for browsing the web if they wish to.
The traders will be prompted that this is a simulation, and that they are in competition with 3 other traders.
They will be run in an Agent Loop with access to the MCP Servers and some tools:
(a) get_state() will provide the time elapsed, time to go, total portfolio value, cash balance, holdings, P&L. They will also receive the portfolio value of the other traders.
(b) trade(ticker, quantity) will buy/sell a ticker with negative quantity

OpenAI Agents SDK will be used for each of the traders.

## Use of OpenAI Agents SDK

Use idiomatic, simple OpenAI Agents SDK approaches, consulting the official OpenAI docs.
Use MCP servers via the context manager as documented.
Use the OpenAI ctx object to manage the trader_id that is used for get_state() and trade() in a clean and idiomatic way.
Use async code; stream back each tool call and decision.

## Architecture

In `backend/environment`:

The code to manage the trading accounts of the players in a local database, keyed off the trader_id. There's a reference implementation from a different project in here of `accounts.py` but this should be completely rewritten. And the SQLLite database goes in this directory and needs to be .gitignored.

The code to make an API call to Massive in order to look up the official equity prices.
MASSIVE_API_KEY is in the .env file.

In `backend/traders`

The code to create the MCPStdioServers, perhaps in mcp_servers.py
The code to run each Agent Loop, perhaps in trader.py
The prompts in templates.py

In `backend/arena`

The management code to set up and run the trading floor
The configuration for the 4 traders
The code to 'start' the arena at the begining, and to 'end' the arena at the end, which should effectively sell all open positions to end up with a cash number. (The traders should be informed that this will happen in their initial prompt).

In `backend/api`

The FastAPI app that will service the frontend.

In `backend/test`

Rigorous testing for everything.
For testing, it's fine to use a real connection to OpenRouter; the API key is in the .env file and you should use model "openai/gpt-oss-120b" which is cheap.
It's also fine to use the real Massive API and MASSIVE_API_KEY in the .env file; I have a pro plan.

In `frontend`

A simple Vite vanilla TS user interface.
For simplicity, the UI will drive the process (so we don't need long running backend)
The UI will have at the top:
- A 'Start' button to reset portfolios and start the 1 hour, a 'Stop' button to end it (happens automatically after an hour)
- A large countdown clock counting down the hour
- A dark mode / light mode switch

Then the UI will be divided into 4 panels for each trader:

Top of the panel: current portfolio value, in large font, green if Profit overall and Red if loss.
Beneath that, a line chart showing the change in value of the portfolio over the course of the 1 hour, populating gradually from left to right.
Beneath that, the key facts: Cash, P&L
Beneath that, a heatmap of the current portfolio holdings with the stock ticker, the size reflects how much is owned, the color represents gain/loss.
Beneath that, a log trace showing the Trader use of tools and trading decisions (will this even be possible for MCP server use?)

The UI will send 'tick' messages to the server API so that it updates pricing information for all portfolios and responds to the UI to refresh the P&L charts.

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)
- And elegant shades of greys. Avoid overuse of gradients and other LLM-generated tells. Never any emojis.

In `scripts`

`start_mac.sh`
and
`stop_mac.sh`

That starts up a docker container (described by a single Dockerfile in the project root) that contains a statically compiled frontend served at / and the backend in a uv project

## Questions / issues

### Issue 1

The initial LLMs to support for trading are:
Claude Opus 4.7 on Max reasoning mode
OpenAI GPT 5.4 on xhigh reasoning mode
And 2 other models via OpenRouter on their highest reasoning mode, 1 of which will be Kimi K2.6.

I'm concerned that I don't know how the OpenAI Agents SDK will support the passthrough of parameters to ensure that LLMs are on the maximum reasoning effort; I suspect this is handled differently for OpenAI, Anthropic and OpenRouter.

### Issue 2

I want to be able to stream back results from the Agent Loops all the way to the UI, but I don't know if this is technically possible.

### Issue 3

I'd like the use of MCP servers to be recorded in the Trader log, but that might not be possible