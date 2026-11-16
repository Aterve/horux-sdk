package io.horux.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

/**
 * The one place an {@link ObjectMapper} is built for this client.
 *
 * <p>The ingestor decodes {@code timestamp} into Go's {@code *time.Time}, whose
 * {@code UnmarshalJSON} accepts <em>only</em> an RFC 3339 string - a numeric
 * epoch fails with "input is not a JSON string", the handler answers
 * {@code 400 invalid request body}, and the dispatcher classifies 400 as
 * permanent and drops the batch. Jackson 2.x enables
 * {@link SerializationFeature#WRITE_DATES_AS_TIMESTAMPS} by default, so an
 * {@code Instant} serialises as {@code 1760000000.123456789} unless it is
 * disabled here: every push a client made was rejected and discarded.
 *
 * <p>It is a shared factory rather than a line in each client's constructor
 * because that is how the defect arose - two independently constructed mappers,
 * neither configured, with nothing to compare against.
 */
final class Json {
    private Json() {
    }

    /** A mapper that writes temporal values in the wire format the ingestor accepts. */
    static ObjectMapper newMapper() {
        ObjectMapper mapper = new ObjectMapper();
        mapper.registerModule(new JavaTimeModule());
        mapper.disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        return mapper;
    }
}
