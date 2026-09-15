# Spring Boot MCP Server

A Spring Boot application that exposes MCP (Model Context Protocol) tools using Spring AI MCP server starter. The tools are discovered and called by the `a2a-adk-2-0` Python agent through streamable HTTP.

## Tools

The server exposes the following MCP tools:

- `getStockPrice(symbol)` — mock stock price for a ticker.
- `convertCurrency(amount, from, to)` — mock currency conversion.
- `getCurrentDateTime()` — current ISO timestamp.
- `sendEmail(to, subject, body)` — mock email confirmation.
- `getWeather(city)` — mock weather report.

### Banking tools

Mock banking tools backing the Transaction, Cards and Accounts sub-agents. Tool names carry a domain prefix
(`transaction_`, `cards_`, `accounts_`) so the Python agent can group them per sub-agent by prefix. All tools
return plain strings or JSON strings.

**Transactions** (`TransactionTools.java`)

- `transaction_getTransactions(limit?, merchant?, startDate?, endDate?, minAmount?, maxAmount?, txnType?)` —
  filters an in-memory list of ~60 mock transactions and returns a JSON array (newest first) of
  `{id, date, merchant, amount, type}`. All parameters are optional:
  - `limit` (int) — max results, default 20.
  - `merchant` (string) — case-insensitive substring match, e.g. `"amazon"`.
  - `startDate` / `endDate` (string) — inclusive ISO dates `YYYY-MM-DD`.
  - `minAmount` / `maxAmount` (double) — inclusive amount bounds.
  - `txnType` (string) — `"credit"` or `"debit"`.

**Cards** (`CardsTools.java`) — mock cards `CARD-1001`, `CARD-1002`, `CARD-1003`

- `cards_listCards()` — JSON array of all cards.
- `cards_getCardDetails(cardId)` — JSON for one card.
- `cards_blockCard(cardId)` — marks the card `blocked`.
- `cards_getCardLimit(cardId)` — limit and available credit.
- `cards_setCardLimit(cardId, limit)` — sets a new positive limit.

**Accounts** (`AccountsTools.java`) — mock accounts `ACC-001`, `ACC-002`, `ACC-003`

- `accounts_listAccounts()` — JSON array of all accounts.
- `accounts_getBalance(accountId)` — current balance string.
- `accounts_getAccountDetails(accountId)` — JSON for one account.
- `accounts_getStatement(accountId, startDate, endDate)` — mock statement for an inclusive `YYYY-MM-DD` range.

After starting the updated server, refresh the Python agent's tool cache (`POST /refresh-tools` or restart the
agent) so the new tools are discovered.

## Run locally

```bash
cd mcp-server
./mvnw spring-boot:run
```

The MCP server listens on `http://localhost:8080` and the MCP endpoint is `http://localhost:8080/mcp`.

## Protocol

The server uses the `STREAMABLE` MCP protocol over WebMVC:

```yaml
spring:
  ai:
    mcp:
      server:
        protocol: STREAMABLE
        streamable-http:
          mcp-endpoint: /mcp
```

## Build and test

```bash
cd mcp-server
./mvnw clean package
```

## Project layout

```
mcp-server/
├── pom.xml
├── src/main/java/com/example/mcp/server/
│   ├── McpServerApplication.java
│   └── tools/
│       ├── AccountsTools.java
│       ├── CardsTools.java
│       ├── FinanceTools.java
│       ├── TransactionTools.java
│       ├── UtilityTools.java
│       └── WeatherTools.java
└── src/main/resources/application.yml
```

## Adding a new tool

1. Create or edit a `@Component` class in `src/main/java/com/example/mcp/server/tools/`.
2. Add a public method annotated with `@McpTool(name = "...", description = "...")`.
3. Annotate parameters with `@McpToolParam(description = "...", required = true)`.
4. Restart the MCP server.
5. In the Python agent, call `POST /refresh-tools` or restart the agent server to refresh the tool cache.
