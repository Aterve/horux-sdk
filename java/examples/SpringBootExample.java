import io.horux.client.*;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.web.bind.annotation.*;
import org.springframework.stereotype.Component;

import javax.servlet.*;
import javax.servlet.http.*;
import java.io.IOException;
import java.util.HashMap;
import java.util.Map;

/**
 * Spring Boot example showing HTTP request monitoring with Horux.
 *
 * This example demonstrates:
 * - Configuring Horux as a Spring Bean
 * - HTTP request/response tracking with a Filter
 * - Business metrics in controllers
 * - Error logging
 */
@SpringBootApplication
public class SpringBootExample {

    public static void main(String[] args) {
        SpringApplication.run(SpringBootExample.class, args);
    }

    /**
     * Configure Horux client as a Spring Bean.
     */
    @Bean
    public HoruxClient horuxClient() {
        HoruxConfig config = HoruxConfig.builder()
                .apiToken(System.getenv("HORUX_API_TOKEN"))
                .resourceId(System.getenv("HORUX_RESOURCE_ID"))
                .fromEnvironment()
                .build();

        return new HoruxClient(config);
    }

    /**
     * HTTP request monitoring filter.
     */
    @Component
    public static class HoruxMonitoringFilter implements Filter {
        private final HoruxClient horux;

        public HoruxMonitoringFilter(HoruxClient horux) {
            this.horux = horux;
        }

        @Override
        public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
                throws IOException, ServletException {

            HttpServletRequest httpRequest = (HttpServletRequest) request;
            HttpServletResponse httpResponse = (HttpServletResponse) response;

            long startTime = System.currentTimeMillis();

            try {
                chain.doFilter(request, response);
            } finally {
                long duration = System.currentTimeMillis() - startTime;

                // Track request count
                Map<String, String> labels = new HashMap<>();
                labels.put("method", httpRequest.getMethod());
                labels.put("path", httpRequest.getRequestURI());
                labels.put("status", String.valueOf(httpResponse.getStatus()));
                horux.metrics.counter("http.requests.total", 1, labels);

                // Track response time
                Map<String, String> timeLabels = new HashMap<>();
                timeLabels.put("method", httpRequest.getMethod());
                timeLabels.put("path", httpRequest.getRequestURI());
                horux.metrics.gauge("http.request.duration", duration, timeLabels);
            }
        }
    }

    /**
     * Example REST controller with business metrics.
     */
    @RestController
    @RequestMapping("/api")
    public static class OrderController {
        private final HoruxClient horux;

        public OrderController(HoruxClient horux) {
            this.horux = horux;
        }

        @PostMapping("/orders")
        public Map<String, String> createOrder(@RequestBody Map<String, Object> orderData) {
            try {
                // Simulate order creation
                String orderId = "order-" + System.currentTimeMillis();

                // Track business metric
                Map<String, String> labels = new HashMap<>();
                labels.put("payment_method", (String) orderData.getOrDefault("paymentMethod", "unknown"));
                horux.metrics.counter("orders.created", 1, labels);

                // Log the event
                Map<String, Object> logFields = new HashMap<>();
                logFields.put("orderId", orderId);
                logFields.put("userId", orderData.get("userId"));
                logFields.put("amount", orderData.get("amount"));
                horux.logs.info("Order created successfully", logFields);

                Map<String, String> result = new HashMap<>();
                result.put("orderId", orderId);
                result.put("status", "created");
                return result;

            } catch (Exception e) {
                // Track error
                Map<String, String> errorLabels = new HashMap<>();
                errorLabels.put("type", e.getClass().getSimpleName());
                horux.metrics.counter("errors.total", 1, errorLabels);

                // Log error
                Map<String, Object> errorFields = new HashMap<>();
                errorFields.put("error", e.getMessage());
                errorFields.put("stack", e.getStackTrace()[0].toString());
                horux.logs.error("Failed to create order", errorFields);

                throw new RuntimeException("Order creation failed", e);
            }
        }

        @GetMapping("/health")
        public Map<String, String> health() {
            Map<String, String> status = new HashMap<>();
            status.put("status", "ok");
            status.put("timestamp", String.valueOf(System.currentTimeMillis()));
            return status;
        }
    }
}
