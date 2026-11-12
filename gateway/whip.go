package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/pion/interceptor"
	"github.com/pion/sdp/v3"
	"github.com/pion/webrtc/v4"
)

type whipConfig struct {
	URL   string `json:"url"`
	Token string `json:"token"`
	Codec string `json:"codec"`
}

func validateWHIP(c whipConfig) error {
	u, err := url.Parse(c.URL)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil || u.Fragment != "" || len(c.URL) > 2048 {
		return errors.New("Enter a valid HTTP or HTTPS WHIP endpoint")
	}
	if c.Codec != "h264" && c.Codec != "av1" && c.Codec != "h265" {
		return errors.New("Choose H264, H265 or AV1")
	}
	if len(c.Token) > 8192 || strings.ContainsAny(c.Token, "\r\n") {
		return errors.New("Invalid stream token")
	}
	return nil
}

// A single outbound peer reuses the encoded media bridge and RTP clock.
// Tokens exist only in memory and are never returned by status or logged.
type whipPublisher struct {
	g       *gateway
	op      sync.Mutex
	mu      sync.Mutex
	cancel  context.CancelFunc
	done    chan struct{}
	cfg     whipConfig
	state   string
	detail  string
	attempt int
	peer    *viewer
}

var sendStatusClockOrigin = time.Now()

func (p *whipPublisher) status() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	s := map[string]any{"enabled": p.cancel != nil, "state": p.state, "detail": p.detail, "codec": p.cfg.Codec, "attempts": p.attempt, "audio_frames": uint64(0), "video_frames": uint64(0), "video_bytes": uint64(0), "audio_bytes": uint64(0), "video_drops": uint64(0), "audio_drops": uint64(0)}
	if p.peer != nil {
		s["video_bytes"] = p.peer.sentBytes[0].Load()
		s["audio_bytes"] = p.peer.sentBytes[1].Load()
		s["video_drops"] = p.peer.videoDrops.Load()
		s["audio_drops"] = p.peer.audioDrops.Load()
		s["video_packets"] = p.peer.sentPackets[0].Load()
		s["video_frames"] = p.peer.sentVideoFrames.Load()
		s["audio_frames"] = p.peer.sentAudioFrames.Load()
		s["send_session_id"] = p.peer.id
		s["audio_packets"] = p.peer.sentPackets[1].Load()
	}
	s["send_sample_time_ms"] = float64(time.Since(sendStatusClockOrigin).Nanoseconds()) / 1e6
	return s
}
func (p *whipPublisher) update(state, detail string) {
	p.mu.Lock()
	p.state = state
	p.detail = detail
	p.mu.Unlock()
}
func (p *whipPublisher) stopLocked() {
	p.mu.Lock()
	cancel, done := p.cancel, p.done
	p.mu.Unlock()
	if cancel != nil {
		cancel()
		<-done
	}
	p.mu.Lock()
	p.cancel = nil
	p.peer = nil
	p.cfg.Token = ""
	p.state = "stopped"
	p.detail = ""
	p.mu.Unlock()
}
func (p *whipPublisher) stop() { p.op.Lock(); defer p.op.Unlock(); p.stopLocked() }
func (p *whipPublisher) start(c whipConfig) error {
	if err := validateWHIP(c); err != nil {
		return err
	}
	if c.Codec == "av1" && p.g.codec != "av1" {
		return errors.New("AV1 is not enabled in this sender; select H264 or enable AV1 in Setup")
	}
	p.op.Lock()
	defer p.op.Unlock()
	p.stopLocked()
	ctx, cancel := context.WithCancel(context.Background())
	p.mu.Lock()
	p.cfg = c
	p.cancel = cancel
	p.done = make(chan struct{})
	p.attempt = 0
	p.state = "waiting for source"
	p.mu.Unlock()
	go p.run(ctx, c, p.done)
	return nil
}
func (p *whipPublisher) sourceReady(codec string) bool {
	p.g.mu.Lock()
	defer p.g.mu.Unlock()
	last := p.g.lastVideo
	if codec == "h264" && p.g.codec == "av1" {
		last = p.g.lastH264
	}
	if codec == "h265" {
		last = p.g.lastH265
	}
	return p.g.input != nil && !last.IsZero() && time.Since(last) < 3*time.Second
}

// sourceLost reports a source that stayed away longer than a pipeline restart takes. A running
// session is held through shorter gaps: the receiver freezes the picture instead of
// reconnecting and synchronising its output again.
func (p *whipPublisher) sourceLost(codec string) bool {
	p.g.mu.Lock()
	defer p.g.mu.Unlock()
	last := p.g.lastVideo
	if codec == "h264" && p.g.codec == "av1" {
		last = p.g.lastH264
	}
	if codec == "h265" {
		last = p.g.lastH265
	}
	return last.IsZero() || time.Since(last) >= inputGrace
}
func (p *whipPublisher) run(ctx context.Context, c whipConfig, done chan struct{}) {
	defer close(done)
	for ctx.Err() == nil {
		if !p.sourceReady(c.Codec) {
			p.update("waiting for source", "Start the selected source in Dashboard")
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Second):
				continue
			}
		}
		p.mu.Lock()
		p.attempt++
		p.mu.Unlock()
		p.update("connecting", "")
		err := p.session(ctx, c)
		if ctx.Err() != nil {
			return
		}
		p.update("retrying", err.Error())
		select {
		case <-ctx.Done():
			return
		case <-time.After(3 * time.Second):
		}
	}
}

func whipHTTPClient() *http.Client {
	return &http.Client{Timeout: 12 * time.Second, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 5 {
			return errors.New("too many redirects")
		}
		// Never leak stream credentials through a cross-origin redirect.
		if req.URL.Scheme != via[0].URL.Scheme || req.URL.Host != via[0].URL.Host {
			return errors.New("cross-origin redirect requires using the final WHIP URL")
		}
		if req.Method != via[0].Method {
			return errors.New("WHIP redirect must preserve POST")
		}
		return nil
	}}
}

func (p *whipPublisher) session(ctx context.Context, c whipConfig) error {
	g := p.g
	v := &viewer{id: "whip-" + uuid.NewString(), codec: c.Codec, done: make(chan struct{}), needsKey: true, created: time.Now(), clock: &mediaClock{pairs: make(map[uint32]clockPair)}}
	v.queues = [2]chan *frame{make(chan *frame, 5), make(chan *frame, 10)}
	media := &webrtc.MediaEngine{}
	if err := media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: g.capability(c.Codec), PayloadType: 111}, webrtc.RTPCodecTypeVideo); err != nil {
		return err
	}
	media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000, SDPFmtpLine: "apt=111"}, PayloadType: 112}, webrtc.RTPCodecTypeVideo)
	media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2, SDPFmtpLine: "minptime=10;useinbandfec=1;stereo=1"}, PayloadType: 109}, webrtc.RTPCodecTypeAudio)
	registry := &interceptor.Registry{}
	registry.Add(&clockFactory{v.clock})
	if err := webrtc.RegisterDefaultInterceptors(media, registry); err != nil {
		return err
	}
	settings := webrtc.SettingEngine{}
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	if g.mux != nil {
		settings.SetICEUDPMux(g.mux)
	}
	settings.SetSTUNGatherTimeout(3 * time.Second)
	conf := webrtc.Configuration{}
	if len(g.stunURLs) > 0 {
		conf.ICEServers = []webrtc.ICEServer{{URLs: g.stunURLs}}
	}
	api := webrtc.NewAPI(webrtc.WithMediaEngine(media), webrtc.WithInterceptorRegistry(registry), webrtc.WithSettingEngine(settings))
	var err error
	v.pc, err = api.NewPeerConnection(conf)
	if err != nil {
		return err
	}
	defer func() {
		g.remove(v)
		v.pc.Close()
		p.mu.Lock()
		if p.peer == v {
			p.peer = nil
		}
		p.mu.Unlock()
	}()
	v.video, err = webrtc.NewTrackLocalStaticRTP(g.capability(c.Codec), "video", "fastvideo-program")
	if err != nil {
		return err
	}
	v.audio, err = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "audio", "fastvideo-program")
	if err != nil {
		return err
	}
	for kind, track := range []*webrtc.TrackLocalStaticRTP{v.video, v.audio} {
		transceiver, err := v.pc.AddTransceiverFromTrack(track, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionSendonly})
		if err != nil {
			return err
		}
		sender := transceiver.Sender()
		v.ssrc[kind] = uint32(sender.GetParameters().Encodings[0].SSRC)
		go g.feedback(v, sender)
	}
	offer, err := v.pc.CreateOffer(nil)
	if err != nil {
		return err
	}
	gathered := webrtc.GatheringCompletePromise(v.pc)
	if err = v.pc.SetLocalDescription(offer); err != nil {
		return err
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-gathered:
	case <-time.After(8 * time.Second):
		return errors.New("ICE gathering timed out")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL, strings.NewReader(v.pc.LocalDescription().SDP))
	if err != nil {
		return errors.New("Invalid WHIP URL")
	}
	req.Header.Set("Content-Type", "application/sdp")
	req.Header.Set("Accept", "application/sdp")
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	client := whipHTTPClient()
	response, err := client.Do(req)
	if err != nil {
		return errors.New("WHIP endpoint unreachable or redirect rejected; check address and network")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		return fmt.Errorf("WHIP receiver returned HTTP %d; check endpoint, token and codec", response.StatusCode)
	}
	location := response.Header.Get("Location")
	if location == "" {
		return errors.New("WHIP receiver did not return a session Location")
	}
	resource, err := response.Request.URL.Parse(location)
	if err != nil || resource.User != nil || resource.Host != response.Request.URL.Host || resource.Scheme != response.Request.URL.Scheme {
		return errors.New("Unsupported WHIP session Location")
	}
	defer func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		deletion, _ := http.NewRequestWithContext(cleanupCtx, http.MethodDelete, resource.String(), nil)
		if c.Token != "" {
			deletion.Header.Set("Authorization", "Bearer "+c.Token)
		}
		if result, err := client.Do(deletion); err == nil {
			result.Body.Close()
		}
	}()
	if !strings.HasPrefix(strings.ToLower(response.Header.Get("Content-Type")), "application/sdp") {
		return errors.New("WHIP receiver did not return SDP")
	}
	answer, err := io.ReadAll(io.LimitReader(response.Body, 65537))
	if err != nil || len(answer) > 65536 {
		return errors.New("Invalid WHIP answer size")
	}
	var remote sdp.SessionDescription
	if err = remote.Unmarshal(answer); err != nil {
		return errors.New("Invalid WHIP SDP answer")
	}
	acceptedVideo := false
	for _, media := range remote.MediaDescriptions {
		if media.MediaName.Media == "video" && media.MediaName.Port.Value > 0 {
			for _, attr := range media.Attributes {
				if attr.Key == "rtpmap" && strings.Contains(strings.ToLower(attr.Value), c.Codec+"/90000") {
					acceptedVideo = true
				}
			}
		}
	}
	if !acceptedVideo {
		return fmt.Errorf("WHIP receiver does not accept %s video", strings.ToUpper(c.Codec))
	}
	if err = v.pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: string(answer)}); err != nil {
		return errors.New("WHIP SDP answer rejected; check codec support")
	}
	g.mu.Lock()
	g.viewers[v.id] = v
	g.mu.Unlock()
	p.mu.Lock()
	p.peer = v
	p.mu.Unlock()
	go g.send(v, 0)
	go g.send(v, 1)
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	started := time.Now()
	wasConnected := false
	var lostSince time.Time
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-v.done:
			return errors.New("Media session closed; reconnecting")
		case <-ticker.C:
			state := v.pc.ConnectionState()
			if state == webrtc.PeerConnectionStateConnected {
				if !wasConnected {
					wasConnected = true
					g.requestKey()
				}
				lostSince = time.Time{}
				if p.sourceLost(c.Codec) {
					return errors.New("Source interrupted; waiting for fresh media")
				}
				if p.sourceReady(c.Codec) {
					p.update("connected", "")
				} else {
					p.update("connected", "Source interrupted; holding the connection")
				}
			} else if state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateClosed {
				return errors.New("WebRTC connection failed; reconnecting")
			} else if wasConnected {
				if lostSince.IsZero() {
					lostSince = time.Now()
				}
				if time.Since(lostSince) > 3*time.Second {
					return errors.New("WebRTC disconnected; reconnecting")
				}
			} else if time.Since(started) > 15*time.Second {
				return errors.New("WebRTC connection timed out; check receiver UDP/firewall")
			}
		}
	}
}

func (p *whipPublisher) handle(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	switch r.Method {
	case "GET":
	case "POST":
		var c whipConfig
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 12288)).Decode(&c); err != nil {
			http.Error(w, "Invalid WHIP settings", 400)
			return
		}
		c.URL = strings.TrimSpace(c.URL)
		c.Token = strings.TrimSpace(c.Token)
		if err := p.start(c); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
	case "DELETE":
		p.stop()
	default:
		http.Error(w, "Method not allowed", 405)
		return
	}
	json.NewEncoder(w).Encode(p.status())
}
