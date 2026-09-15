package com.example.mcp.server.tools;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.stream.Collectors;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class TransactionTools {

    public record Transaction(String id, LocalDate date, String merchant, double amount, String type) {

        String toJson() {
            return String.format(Locale.ROOT,
                    "{\"id\":\"%s\",\"date\":\"%s\",\"merchant\":\"%s\",\"amount\":%.2f,\"type\":\"%s\"}", id, date,
                    merchant, amount, type);
        }
    }

    private static final String[] MERCHANTS = { "Amazon", "Starbucks", "Uber", "Netflix", "Walmart", "Apple",
            "Shell", "Whole Foods", "Spotify", "Delta Airlines", "Target", "Payroll Inc" };

    private final List<Transaction> transactions;

    public TransactionTools() {
        this(new Random(42), 60);
    }

    public TransactionTools(Random random, int count) {
        List<Transaction> list = new ArrayList<>();
        LocalDate today = LocalDate.now();
        for (int i = 0; i < count; i++) {
            String merchant = MERCHANTS[random.nextInt(MERCHANTS.length)];
            boolean credit = merchant.equals("Payroll Inc") || random.nextInt(10) == 0;
            double amount = credit ? 500.0 + random.nextDouble() * 3000.0 : 5.0 + random.nextDouble() * 400.0;
            list.add(new Transaction(String.format("TXN-%04d", i + 1), today.minusDays(random.nextInt(120)),
                    merchant, Math.round(amount * 100.0) / 100.0, credit ? "credit" : "debit"));
        }
        list.sort((a, b) -> b.date().compareTo(a.date()));
        this.transactions = List.copyOf(list);
    }

    List<Transaction> allTransactions() {
        return transactions;
    }

    @McpTool(name = "transaction_getTransactions", description = "Get a list of the user's bank transactions. "
            + "All filters are optional; omit a filter to not restrict on it. Returns a JSON array of transactions "
            + "with fields id, date (YYYY-MM-DD), merchant, amount, type (credit or debit), newest first.")
    public String getTransactions(
            @McpToolParam(description = "Maximum number of transactions to return, e.g. 10. Defaults to 20 when omitted.", required = false) Integer limit,
            @McpToolParam(description = "Case-insensitive substring to match against the merchant/description, e.g. \"amazon\" or \"uber\".", required = false) String merchant,
            @McpToolParam(description = "Inclusive start date in ISO format YYYY-MM-DD, e.g. 2024-01-01. Only transactions on or after this date are returned.", required = false) String startDate,
            @McpToolParam(description = "Inclusive end date in ISO format YYYY-MM-DD, e.g. 2024-01-31. Only transactions on or before this date are returned.", required = false) String endDate,
            @McpToolParam(description = "Minimum transaction amount (inclusive) as a decimal number, e.g. 50.00.", required = false) Double minAmount,
            @McpToolParam(description = "Maximum transaction amount (inclusive) as a decimal number, e.g. 500.00.", required = false) Double maxAmount,
            @McpToolParam(description = "Transaction type filter: \"credit\" (money in) or \"debit\" (money out). Omit for both.", required = false) String txnType) {
        LocalDate start;
        LocalDate end;
        try {
            start = parseDate(startDate);
            end = parseDate(endDate);
        }
        catch (DateTimeParseException e) {
            return "{\"error\":\"Dates must be in ISO format YYYY-MM-DD\"}";
        }
        if (txnType != null && !txnType.isBlank() && !txnType.equalsIgnoreCase("credit")
                && !txnType.equalsIgnoreCase("debit")) {
            return "{\"error\":\"txnType must be 'credit' or 'debit'\"}";
        }

        List<Transaction> result = filter(limit, merchant, start, end, minAmount, maxAmount, txnType);
        return "[" + result.stream().map(Transaction::toJson).collect(Collectors.joining(",")) + "]";
    }

    public List<Transaction> filter(Integer limit, String merchant, LocalDate start, LocalDate end, Double minAmount,
            Double maxAmount, String txnType) {
        int max = (limit == null || limit <= 0) ? 20 : limit;
        String merchantNeedle = (merchant == null || merchant.isBlank()) ? null : merchant.toLowerCase(Locale.ROOT);
        String type = (txnType == null || txnType.isBlank()) ? null : txnType.toLowerCase(Locale.ROOT);
        return transactions.stream()
                .filter(t -> merchantNeedle == null || t.merchant().toLowerCase(Locale.ROOT).contains(merchantNeedle))
                .filter(t -> start == null || !t.date().isBefore(start))
                .filter(t -> end == null || !t.date().isAfter(end))
                .filter(t -> minAmount == null || t.amount() >= minAmount)
                .filter(t -> maxAmount == null || t.amount() <= maxAmount)
                .filter(t -> type == null || t.type().equals(type))
                .limit(max)
                .toList();
    }

    private static LocalDate parseDate(String value) {
        return (value == null || value.isBlank()) ? null : LocalDate.parse(value.trim());
    }
}
