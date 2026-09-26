package com.example.mcp.server.config;

import java.io.IOException;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Protects the MCP streamable HTTP endpoint with a Bearer token.
 *
 * If {@code mcp.api.key} is not set, the filter is transparent.
 * When set, every request to {@code /mcp} must include:
 * {@code Authorization: Bearer <mcp.api.key>}.
 */
@Configuration
public class McpApiKeyFilterConfig {

	@Bean
	public FilterRegistrationBean<McpApiKeyFilter> mcpApiKeyFilter(
			@Value("${mcp.api.key:}") String apiKey) {
		FilterRegistrationBean<McpApiKeyFilter> registration = new FilterRegistrationBean<>();
		registration.setFilter(new McpApiKeyFilter(apiKey));
		registration.addUrlPatterns("/mcp", "/mcp/*");
		registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
		return registration;
	}

	static class McpApiKeyFilter extends OncePerRequestFilter {

		private final String apiKey;

		McpApiKeyFilter(String apiKey) {
			this.apiKey = apiKey;
		}

		@Override
		protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
				FilterChain filterChain) throws ServletException, IOException {
			if (apiKey == null || apiKey.isBlank()) {
				filterChain.doFilter(request, response);
				return;
			}

			String authHeader = request.getHeader("Authorization");
			String provided = null;
			if (authHeader != null && authHeader.startsWith("Bearer ")) {
				provided = authHeader.substring(7).trim();
			}

			if (provided == null || !provided.equals(apiKey)) {
				response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
				response.setContentType("application/json");
				response.getWriter().write(
						"{\"jsonRpcError\":{\"code\":-32001,\"message\":\"Unauthorized\"}}");
				return;
			}

			filterChain.doFilter(request, response);
		}
	}
}
