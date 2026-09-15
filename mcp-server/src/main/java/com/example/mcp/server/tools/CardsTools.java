package com.example.mcp.server.tools;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Collectors;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class CardsTools {

    static final class Card {
        final String id;
        final String type;
        final String network;
        final String last4;
        final String expiry;
        double limit;
        double balance;
        String status;

        Card(String id, String type, String network, String last4, String expiry, double limit, double balance) {
            this.id = id;
            this.type = type;
            this.network = network;
            this.last4 = last4;
            this.expiry = expiry;
            this.limit = limit;
            this.balance = balance;
            this.status = "active";
        }

        String toJson() {
            return String.format(Locale.ROOT,
                    "{\"cardId\":\"%s\",\"type\":\"%s\",\"network\":\"%s\",\"last4\":\"%s\",\"expiry\":\"%s\","
                            + "\"limit\":%.2f,\"balance\":%.2f,\"status\":\"%s\"}",
                    id, type, network, last4, expiry, limit, balance, status);
        }
    }

    private final Map<String, Card> cards = new LinkedHashMap<>();

    public CardsTools() {
        cards.put("CARD-1001", new Card("CARD-1001", "credit", "Visa", "4242", "09/27", 5000.0, 1234.56));
        cards.put("CARD-1002", new Card("CARD-1002", "debit", "Mastercard", "8811", "03/26", 2000.0, 0.0));
        cards.put("CARD-1003", new Card("CARD-1003", "credit", "Amex", "0005", "12/28", 15000.0, 4821.10));
    }

    Card find(String cardId) {
        return cardId == null ? null : cards.get(cardId.trim().toUpperCase(Locale.ROOT));
    }

    private static String notFound(String cardId) {
        return String.format("{\"error\":\"Card %s not found\"}", cardId);
    }

    @McpTool(name = "cards_listCards", description = "List all payment cards belonging to the user. Returns a JSON array "
            + "with cardId, type (credit/debit), network, last4, expiry, limit, balance and status.")
    public String listCards() {
        return "[" + cards.values().stream().map(Card::toJson).collect(Collectors.joining(",")) + "]";
    }

    @McpTool(name = "cards_getCardDetails", description = "Get details of a single card by its card ID.")
    public String getCardDetails(
            @McpToolParam(description = "Card identifier, e.g. CARD-1001. Use cards_listCards to discover IDs.", required = true) String cardId) {
        Card card = find(cardId);
        return card == null ? notFound(cardId) : card.toJson();
    }

    @McpTool(name = "cards_blockCard", description = "Block (freeze) a card so it can no longer be used for payments.")
    public String blockCard(
            @McpToolParam(description = "Card identifier to block, e.g. CARD-1001.", required = true) String cardId) {
        Card card = find(cardId);
        if (card == null) {
            return notFound(cardId);
        }
        card.status = "blocked";
        return String.format("Card %s ending in %s has been blocked.", card.id, card.last4);
    }

    @McpTool(name = "cards_getCardLimit", description = "Get the spending limit and available credit of a card.")
    public String getCardLimit(
            @McpToolParam(description = "Card identifier, e.g. CARD-1001.", required = true) String cardId) {
        Card card = find(cardId);
        if (card == null) {
            return notFound(cardId);
        }
        return String.format(Locale.ROOT, "{\"cardId\":\"%s\",\"limit\":%.2f,\"available\":%.2f}", card.id,
                card.limit, card.limit - card.balance);
    }

    @McpTool(name = "cards_setCardLimit", description = "Set a new spending limit on a card.")
    public String setCardLimit(
            @McpToolParam(description = "Card identifier, e.g. CARD-1001.", required = true) String cardId,
            @McpToolParam(description = "New spending limit as a positive decimal number, e.g. 7500.00.", required = true) double limit) {
        Card card = find(cardId);
        if (card == null) {
            return notFound(cardId);
        }
        if (limit <= 0) {
            return "{\"error\":\"limit must be greater than zero\"}";
        }
        double previous = card.limit;
        card.limit = limit;
        return String.format(Locale.ROOT, "Limit on card %s changed from $%.2f to $%.2f.", card.id, previous, limit);
    }
}
