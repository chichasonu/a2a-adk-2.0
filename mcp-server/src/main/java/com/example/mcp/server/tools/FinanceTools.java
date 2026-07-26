package com.example.mcp.server.tools;

import java.util.Random;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class FinanceTools {

    private final Random random = new Random();

    @McpTool(name = "getStockPrice", description = "Get a mock current stock price for a ticker symbol")
    public String getStockPrice(
            @McpToolParam(description = "Stock ticker symbol, e.g. AAPL", required = true) String symbol) {
        double price = 50.0 + random.nextDouble() * 200.0;
        return String.format("The current price of %s is $%.2f", symbol.toUpperCase(), price);
    }

    @McpTool(name = "convertCurrency", description = "Convert an amount between two currencies using a mock exchange rate")
    public String convertCurrency(
            @McpToolParam(description = "Amount to convert", required = true) double amount,
            @McpToolParam(description = "Source currency code, e.g. USD", required = true) String from,
            @McpToolParam(description = "Target currency code, e.g. EUR", required = true) String to) {
        double rate = 0.8 + random.nextDouble() * 0.4;
        double converted = amount * rate;
        return String.format("%.2f %s = %.2f %s (rate %.4f)", amount, from.toUpperCase(), converted,
                to.toUpperCase(), rate);
    }
}
