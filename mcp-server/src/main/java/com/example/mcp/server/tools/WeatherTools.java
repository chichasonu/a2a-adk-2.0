package com.example.mcp.server.tools;

import org.springframework.ai.mcp.annotation.McpTool;
import org.springframework.ai.mcp.annotation.McpToolParam;
import org.springframework.stereotype.Component;

@Component
public class WeatherTools {

    @McpTool(name = "getWeather", description = "Get a mock weather report for a city")
    public String getWeather(
            @McpToolParam(description = "City name", required = true) String city) {
        return String.format("The weather in %s is sunny and 25°C.", city);
    }
}
