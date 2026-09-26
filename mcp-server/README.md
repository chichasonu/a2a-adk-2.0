# Spring Boot MCP Server

A Spring Boot application that exposes MCP (Model Context Protocol) tools using Spring AI MCP server starter. The tools are discovered and called by the `a2a-adk-2-0` Python agent through streamable HTTP.

## Tools

The server exposes the following MCP tools:

- `getStockPrice(symbol)` — mock stock price for a ticker.
- `convertCurrency(amount, from, to)` — mock currency conversion.
- `getCurrentDateTime()` — current ISO timestamp.
- `sendEmail(to, subject, body)` — mock email confirmation.
- `getWeather(city)` — mock weather report.

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
│       ├── FinanceTools.java
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
