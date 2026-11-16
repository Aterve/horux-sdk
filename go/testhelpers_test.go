package client

import (
	"encoding/json"
	"io"
	"net/http"
)

func decodeJSON(r *http.Request, target interface{}) error {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return err
	}
	return json.Unmarshal(body, target)
}

func writeJSON(w http.ResponseWriter, payload interface{}) {
	_ = json.NewEncoder(w).Encode(payload)
}
