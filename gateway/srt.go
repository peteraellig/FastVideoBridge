package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	srt "github.com/datarhei/gosrt"
)

type srtConfig struct {
	Host       string `json:"host"`
	Port       int    `json:"port"`
	Latency    int    `json:"latency"`
	StreamID   string `json:"streamid"`
	Passphrase string `json:"passphrase"`
	AES        int    `json:"aes"`
}

func (c srtConfig) validate() error {
	if !regexp.MustCompile(`^[A-Za-z0-9._-]{1,253}$`).MatchString(c.Host) || c.Port < 1 || c.Port > 65535 || c.Latency < 20 || c.Latency > 10000 || (c.AES != 128 && c.AES != 192 && c.AES != 256) || len(c.StreamID) > 512 || strings.ContainsAny(c.StreamID, "\r\n\x00") || (c.Passphrase != "" && (len(c.Passphrase) < 10 || len(c.Passphrase) > 79 || strings.ContainsAny(c.Passphrase, "\r\n\x00"))) {
		return errors.New("Invalid SRT settings")
	}
	return nil
}
func (c srtConfig) transport() srt.Config {
	cfg := srt.DefaultConfig()
	cfg.ConnectionTimeout = 3 * time.Second
	cfg.PeerIdleTimeout = 3 * time.Second
	cfg.Latency = time.Duration(c.Latency) * time.Millisecond
	cfg.StreamId = c.StreamID
	cfg.Passphrase = c.Passphrase
	cfg.PBKeylen = c.AES / 8
	cfg.EnforcedEncryption = true
	cfg.PayloadSize = 1316
	return cfg
}

type srtPublisher struct {
	mu    sync.Mutex
	local net.Conn
	conn  srt.Conn
	state string
	bytes uint64
}

func (p *srtPublisher) stop() {
	p.mu.Lock()
	local, conn := p.local, p.conn
	p.mu.Unlock()
	if local != nil {
		local.Close()
	}
	if conn != nil {
		conn.Close()
	}
}
func (p *srtPublisher) status() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	result := map[string]any{"transport_state": p.state, "sent_bytes": p.bytes, "transport": "datarhei/gosrt · pure Go"}
	if p.conn != nil {
		var stats srt.Statistics
		p.conn.Stats(&stats)
		result["sent_packets"] = stats.Accumulated.PktSentUnique
		result["retransmissions"] = stats.Accumulated.PktRetrans
		result["transport_drops"] = stats.Accumulated.PktSendDrop
		result["send_buffer_ms"] = stats.Instantaneous.MsSendBuf
		result["rtt_ms"] = stats.Instantaneous.MsRTT
	}
	return result
}
func (p *srtPublisher) handle(w http.ResponseWriter, r *http.Request) {
	if r.Method == "DELETE" {
		p.stop()
	} else if r.Method != "GET" {
		http.Error(w, "GET or DELETE required", 405)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(p.status())
}

// Loopback-only feed: uint32 JSON size, private caller settings, one-byte handshake result, MPEG-TS bytes.
func (p *srtPublisher) feed(local net.Conn) {
	defer local.Close()
	p.mu.Lock()
	if p.local != nil {
		p.mu.Unlock()
		return
	}
	p.local = local
	p.state = "connecting"
	p.bytes = 0
	p.mu.Unlock()
	defer func() { p.mu.Lock(); p.local = nil; p.conn = nil; p.state = "stopped"; p.mu.Unlock() }()
	local.SetReadDeadline(time.Now().Add(5 * time.Second))
	var size uint32
	if binary.Read(local, binary.BigEndian, &size) != nil || size == 0 || size > 4096 {
		return
	}
	raw := make([]byte, size)
	if _, err := io.ReadFull(local, raw); err != nil {
		return
	}
	var c srtConfig
	if json.Unmarshal(raw, &c) != nil || c.validate() != nil {
		return
	}
	conn, err := srt.Dial("srt", net.JoinHostPort(c.Host, strconv.Itoa(c.Port)), c.transport())
	c.Passphrase = ""
	clear(raw)
	if err != nil {
		local.Write([]byte{0})
		return
	}
	defer conn.Close()
	p.mu.Lock()
	p.conn = conn
	p.state = "connected"
	p.mu.Unlock()
	local.SetWriteDeadline(time.Now().Add(time.Second))
	if _, err = local.Write([]byte{1}); err != nil {
		return
	}
	buffer := make([]byte, 1316)
	for {
		local.SetReadDeadline(time.Now().Add(3 * time.Second))
		n, readErr := io.ReadFull(local, buffer)
		if n > 0 {
			// Bound unacknowledged live data even though gosrt Write itself is asynchronous.
			var stats srt.Statistics
			conn.Stats(&stats)
			if stats.Instantaneous.ByteSendBuf > 4*1024*1024 {
				return
			}
			written, writeErr := conn.Write(buffer[:n])
			if writeErr != nil {
				return
			}
			p.mu.Lock()
			p.bytes += uint64(written)
			p.state = "sending"
			p.mu.Unlock()
		}
		if readErr != nil {
			return
		}
	}
}
