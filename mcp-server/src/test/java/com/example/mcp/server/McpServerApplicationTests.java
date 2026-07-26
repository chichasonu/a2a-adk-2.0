package com.example.mcp.server;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

@SpringBootTest
@TestPropertySource(properties = "server.port=0")
class McpServerApplicationTests {

	@Test
	void contextLoads() {
	}

}
