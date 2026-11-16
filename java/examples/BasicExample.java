import io.horux.client.*;

import java.util.HashMap;
import java.util.Map;

/**
 * Basic example showing how to use the Horux Java SDK.
 */
public class BasicExample {
    public static void main(String[] args) {
        // Configure the client
        HoruxConfig config = HoruxConfig.builder()
                .apiToken(System.getenv("HORUX_API_TOKEN"))
                .resourceId(System.getenv("HORUX_RESOURCE_ID"))
                .fromEnvironment() // Load additional config from environment
                .build();

        // Create client using try-with-resources for automatic cleanup
        try (HoruxClient client = new HoruxClient(config)) {

            // Send metrics
            System.out.println("Sending metrics...");

            // Counter - always increasing
            Map<String, String> labels = new HashMap<>();
            labels.put("method", "GET");
            labels.put("status", "200");
            client.metrics.counter("http.requests.total", 1, labels);

            // Gauge - can increase or decrease
            Map<String, String> memoryLabels = new HashMap<>();
            memoryLabels.put("type", "heap");
            client.metrics.gauge("memory.used.bytes", 1024.5, memoryLabels);

            // Send logs
            System.out.println("Sending logs...");

            // Info log
            Map<String, Object> userFields = new HashMap<>();
            userFields.put("userId", "123");
            userFields.put("action", "login");
            client.logs.info("User logged in successfully", userFields);

            // Error log
            Map<String, Object> errorFields = new HashMap<>();
            errorFields.put("error", "Connection timeout");
            errorFields.put("retries", 3);
            client.logs.error("Failed to connect to database", errorFields);

            // Warning log
            Map<String, Object> warnFields = new HashMap<>();
            warnFields.put("threshold", 80);
            warnFields.put("current", 85);
            client.logs.warn("Memory usage above threshold", warnFields);

            System.out.println("Data sent successfully!");
            System.out.println("Waiting for background flush...");

            // Give background flusher time to complete
            Thread.sleep(2000);

        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
            e.printStackTrace();
        }
    }
}
