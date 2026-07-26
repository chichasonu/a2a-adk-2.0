package com.example.mcp.server.tools;

import java.time.Instant;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class UtilityTools {

    @McpTool(name = "getCurrentDateTime", description = "Get the current date and time in ISO 8601 format")
    public String getCurrentDateTime() {
        return Instant.now().toString();
    }

    @McpTool(name = "sendEmail", description = "Send a mock email and return a confirmation")
    public String sendEmail(
            @McpToolParam(description = "Recipient email address", required = true) String to,
            @McpToolParam(description = "Email subject", required = true) String subject,
            @McpToolParam(description = "Email body text", required = true) String body) {
        return String.format("Email sent to %s with subject '%s' (body length: %d characters)", to, subject,
                body.length());
    }
}
