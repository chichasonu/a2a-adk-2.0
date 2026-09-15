package com.example.mcp.server.tools;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.stream.Collectors;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class AccountsTools {

    record Account(String id, String type, String currency, double balance, String iban) {

        String toJson() {
            return String.format(Locale.ROOT,
                    "{\"accountId\":\"%s\",\"type\":\"%s\",\"currency\":\"%s\",\"balance\":%.2f,\"iban\":\"%s\"}", id,
                    type, currency, balance, iban);
        }
    }

    private final Map<String, Account> accounts = new LinkedHashMap<>();
    private final Random random = new Random();

    public AccountsTools() {
        accounts.put("ACC-001", new Account("ACC-001", "checking", "USD", 3521.47, "US12 3456 7890 0001"));
        accounts.put("ACC-002", new Account("ACC-002", "savings", "USD", 15800.00, "US12 3456 7890 0002"));
        accounts.put("ACC-003", new Account("ACC-003", "credit", "USD", -1234.56, "US12 3456 7890 0003"));
    }

    Account find(String accountId) {
        return accountId == null ? null : accounts.get(accountId.trim().toUpperCase(Locale.ROOT));
    }

    private static String notFound(String accountId) {
        return String.format("{\"error\":\"Account %s not found\"}", accountId);
    }

    @McpTool(name = "accounts_listAccounts", description = "List all bank accounts belonging to the user. Returns a JSON "
            + "array with accountId, type (checking/savings/credit), currency, balance and iban.")
    public String listAccounts() {
        return "[" + accounts.values().stream().map(Account::toJson).collect(Collectors.joining(",")) + "]";
    }

    @McpTool(name = "accounts_getBalance", description = "Get the current balance of a bank account.")
    public String getBalance(
            @McpToolParam(description = "Account identifier, e.g. ACC-001. Use accounts_listAccounts to discover IDs.", required = true) String accountId) {
        Account account = find(accountId);
        if (account == null) {
            return notFound(accountId);
        }
        return String.format(Locale.ROOT, "Account %s (%s) balance: %.2f %s", account.id(), account.type(),
                account.balance(), account.currency());
    }

    @McpTool(name = "accounts_getAccountDetails", description = "Get full details of a bank account by its ID.")
    public String getAccountDetails(
            @McpToolParam(description = "Account identifier, e.g. ACC-001.", required = true) String accountId) {
        Account account = find(accountId);
        return account == null ? notFound(accountId) : account.toJson();
    }

    @McpTool(name = "accounts_getStatement", description = "Get a mock account statement (opening/closing balance, "
            + "totals and entries) for a date range.")
    public String getStatement(
            @McpToolParam(description = "Account identifier, e.g. ACC-001.", required = true) String accountId,
            @McpToolParam(description = "Inclusive statement start date in ISO format YYYY-MM-DD, e.g. 2024-01-01.", required = true) String startDate,
            @McpToolParam(description = "Inclusive statement end date in ISO format YYYY-MM-DD, e.g. 2024-01-31.", required = true) String endDate) {
        Account account = find(accountId);
        if (account == null) {
            return notFound(accountId);
        }
        LocalDate start;
        LocalDate end;
        try {
            start = LocalDate.parse(startDate.trim());
            end = LocalDate.parse(endDate.trim());
        }
        catch (DateTimeParseException | NullPointerException e) {
            return "{\"error\":\"Dates must be in ISO format YYYY-MM-DD\"}";
        }
        if (end.isBefore(start)) {
            return "{\"error\":\"endDate must not be before startDate\"}";
        }

        int entries = 3 + random.nextInt(5);
        double credits = 0;
        double debits = 0;
        StringBuilder lines = new StringBuilder();
        long span = end.toEpochDay() - start.toEpochDay() + 1;
        for (int i = 0; i < entries; i++) {
            LocalDate date = start.plusDays(random.nextInt((int) Math.min(span, Integer.MAX_VALUE)));
            boolean credit = random.nextInt(4) == 0;
            double amount = Math.round((credit ? 500 + random.nextDouble() * 2000 : 10 + random.nextDouble() * 300) * 100.0)
                    / 100.0;
            if (credit) {
                credits += amount;
            }
            else {
                debits += amount;
            }
            lines.append(String.format(Locale.ROOT, "%s  %-6s  %10.2f%n", date, credit ? "CREDIT" : "DEBIT",
                    amount));
        }
        double closing = account.balance();
        double opening = closing - credits + debits;
        return String.format(Locale.ROOT,
                "Statement for %s (%s) %s to %s%nOpening balance: %.2f %s%n%sTotal credits: %.2f%nTotal debits: %.2f%n"
                        + "Closing balance: %.2f %s",
                account.id(), account.type(), start, end, opening, account.currency(), lines, credits, debits, closing,
                account.currency());
    }
}
