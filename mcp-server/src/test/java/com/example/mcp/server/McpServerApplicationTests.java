package com.example.mcp.server;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.LocalDate;
import java.util.List;
import java.util.Random;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;

import com.example.mcp.server.tools.AccountsTools;
import com.example.mcp.server.tools.CardsTools;
import com.example.mcp.server.tools.TransactionTools;

@SpringBootTest
@TestPropertySource(properties = "server.port=0")
class McpServerApplicationTests {

	@Autowired
	private TransactionTools transactionTools;

	@Autowired
	private CardsTools cardsTools;

	@Autowired
	private AccountsTools accountsTools;

	@Test
	void contextLoads() {
		assertThat(transactionTools).isNotNull();
		assertThat(cardsTools).isNotNull();
		assertThat(accountsTools).isNotNull();
	}

	@Test
	void transactionFiltersApply() {
		TransactionTools tools = new TransactionTools(new Random(7), 200);
		LocalDate start = LocalDate.now().minusDays(30);
		LocalDate end = LocalDate.now().minusDays(1);

		List<TransactionTools.Transaction> result = tools.filter(5, "AMA", start, end, 10.0, 300.0, "debit");

		assertThat(result).hasSizeLessThanOrEqualTo(5);
		assertThat(result).allSatisfy(t -> {
			assertThat(t.merchant().toLowerCase()).contains("ama");
			assertThat(t.date()).isBetween(start, end);
			assertThat(t.amount()).isBetween(10.0, 300.0);
			assertThat(t.type()).isEqualTo("debit");
		});
	}

	@Test
	void transactionToolReturnsJsonAndValidatesDates() {
		String json = transactionTools.getTransactions(3, null, null, null, null, null, null);
		assertThat(json).startsWith("[").endsWith("]").contains("\"id\":\"TXN-");

		String error = transactionTools.getTransactions(null, null, "01/02/2024", null, null, null, null);
		assertThat(error).contains("error").contains("YYYY-MM-DD");
	}

	@Test
	void cardsToolsMutateMockState() {
		assertThat(cardsTools.listCards()).contains("CARD-1001");
		assertThat(cardsTools.blockCard("card-1002")).contains("blocked");
		assertThat(cardsTools.getCardDetails("CARD-1002")).contains("\"status\":\"blocked\"");
		assertThat(cardsTools.setCardLimit("CARD-1001", 7500)).contains("7500.00");
		assertThat(cardsTools.getCardLimit("CARD-1001")).contains("\"limit\":7500.00");
		assertThat(cardsTools.getCardDetails("CARD-9999")).contains("not found");
	}

	@Test
	void accountsToolsReturnMockData() {
		assertThat(accountsTools.listAccounts()).contains("ACC-001", "ACC-002", "ACC-003");
		assertThat(accountsTools.getBalance("ACC-001")).contains("3521.47 USD");
		assertThat(accountsTools.getAccountDetails("ACC-002")).contains("\"type\":\"savings\"");
		assertThat(accountsTools.getStatement("ACC-001", "2024-01-01", "2024-01-31")).contains("Opening balance")
				.contains("Closing balance");
		assertThat(accountsTools.getStatement("ACC-001", "2024-02-01", "2024-01-01")).contains("error");
		assertThat(accountsTools.getBalance("ACC-404")).contains("not found");
	}

}
