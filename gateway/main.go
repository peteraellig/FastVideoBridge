// FastVideo: one loopback encoded-media input, independent bounded WebRTC senders.
package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/pion/ice/v4"
	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/webrtc/v4"
)

type frame struct {
	codec    string
	kind     byte
	key      bool
	pts      int64
	ntp      uint64
	payloads [][]byte
}
type clockPair struct {
	rtp uint32
	ntp uint64
}
type mediaClock struct {
	mu    sync.RWMutex
	pairs map[uint32]clockPair
}
type clockFactory struct{ clock *mediaClock }

func (f *clockFactory) NewInterceptor(string) (interceptor.Interceptor, error) {
	return &clockInterceptor{clock: f.clock}, nil
}

type clockInterceptor struct {
	interceptor.NoOp
	clock *mediaClock
}

func (c *clockInterceptor) BindRTCPWriter(writer interceptor.RTCPWriter) interceptor.RTCPWriter {
	return interceptor.RTCPWriterFunc(func(packets []rtcp.Packet, attrs interceptor.Attributes) (int, error) {
		outgoing := make([]rtcp.Packet, 0, len(packets)*2)
		c.clock.mu.RLock()
		for _, p := range packets {
			if sr, ok := p.(*rtcp.SenderReport); ok {
				pair, valid := c.clock.pairs[sr.SSRC]
				if !valid {
					continue
				}
				copySR := *sr
				copySR.RTPTime = pair.rtp
				copySR.NTPTime = pair.ntp
				outgoing = append(outgoing, &copySR, &rtcp.SourceDescription{Chunks: []rtcp.SourceDescriptionChunk{{Source: sr.SSRC, Items: []rtcp.SourceDescriptionItem{{Type: rtcp.SDESCNAME, Text: "fastvideo-program"}}}}})
			} else {
				outgoing = append(outgoing, p)
			}
		}
		c.clock.mu.RUnlock()
		if len(outgoing) == 0 {
			return 0, nil
		}
		return writer.Write(outgoing, attrs)
	})
}

type viewer struct {
	userID                                             string
	clientKey                                          string
	codec                                              string
	id                                                 string
	pc                                                 *webrtc.PeerConnection
	video, audio                                       *webrtc.TrackLocalStaticRTP
	queues                                             [2]chan *frame
	done                                               chan struct{}
	once                                               sync.Once
	needsKey                                           bool
	clock                                              *mediaClock
	ssrc                                               [2]uint32
	sequence                                           [2]uint16
	created                                            time.Time
	videoDrops, audioDrops, skipped, nacks, plis, firs atomic.Uint64
	sentBytes, sentPackets                             [2]atomic.Uint64
	sentVideoFrames, sentAudioFrames                   atomic.Uint64
	writeMaxUs                                         atomic.Int64
	lastPLI                                            atomic.Int64
	lastSend                                           atomic.Int64
	reportedLoss                                       [2]atomic.Uint32
	reportedJitter                                     [2]atomic.Uint32
}

func videoCapability(codec string) webrtc.RTPCodecCapability {
	capability := webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeH264, ClockRate: 90000, SDPFmtpLine: "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=64002a", RTCPFeedback: []webrtc.RTCPFeedback{{Type: "nack"}, {Type: "nack", Parameter: "pli"}, {Type: "ccm", Parameter: "fir"}}}
	if codec == "av1" {
		capability.MimeType = webrtc.MimeTypeAV1
		capability.SDPFmtpLine = "profile=0;level-idx=8;tier=0"
	}
	if codec == "h265" {
		capability.MimeType = webrtc.MimeTypeH265
		capability.SDPFmtpLine = "profile-id=1;tier-flag=0;level-id=120;sprop-max-don-diff=0"
	}
	return capability
}

type gateway struct {
	codec       string
	mu          sync.Mutex
	epoch       uint64
	viewers     map[string]*viewer
	max         int
	lastH264    time.Time
	lastH265    time.Time
	lastVideo   time.Time
	input       net.Conn
	lastMedia   time.Time
	rebase      timeRebase
	keyMu       sync.Mutex
	lastKey     time.Time
	frames      atomic.Uint64
	packets     atomic.Uint64
	videoDrops  atomic.Uint64
	audioDrops  atomic.Uint64
	mux         *ice.UDPMuxDefault
	keyframeURL string
	stunURLs    []string
	publicIPv4  string
}

// How long connections outlive the encoded-media input. The application restarts its source
// pipeline after a fault or a source change within a few seconds; closing every viewer and the
// WHIP output at once cost the far end a full reconnect (10 to 20 s) each time.
const inputGrace = 10 * time.Second

// timeRebase keeps RTP time continuous across restarts of the application's media timeline.
// Every restart counts pts from zero again. The NTP media time of a frame minus its pts is the
// wall-clock origin of its timeline, so the difference of two origins is exactly the shift that
// continues the first timeline, for video and audio alike (they share one timeline).
type timeRebase struct {
	anchored bool
	pending  bool
	origin   float64 // first timeline: seconds of (ntp - pts), relative to anchorNTP
	anchor   uint64
	shift    float64 // seconds added to the current timeline
	last     float64 // newest rebased media time in seconds
}

// restart is called when a new input connection begins.
func (t *timeRebase) restart() { t.pending = true }

func (t *timeRebase) apply(f *frame) {
	rate := 90000.0
	if f.kind == 1 {
		rate = 48000
	}
	seconds := float64(f.pts) / rate
	if !t.anchored {
		t.anchored, t.pending, t.anchor, t.origin, t.shift = true, false, f.ntp, -seconds, 0
	} else if t.pending {
		t.pending = false
		origin := float64(int64(f.ntp-t.anchor))/4294967296 - seconds
		t.shift = origin - t.origin
		// Never step back behind what was already sent (e.g. a wall clock that was set back).
		if seconds+t.shift <= t.last {
			t.shift = t.last - seconds + 0.02
		}
	}
	if seconds+t.shift > t.last {
		t.last = seconds + t.shift
	}
	if t.shift != 0 {
		f.pts += int64(math.Round(t.shift * rate))
	}
}

func parseFrame(r io.Reader) (*frame, error) {
	h := make([]byte, 28)
	if _, err := io.ReadFull(r, h); err != nil {
		return nil, err
	}
	if string(h[:4]) != "FV01" || h[4] > 3 || h[5] > 1 || binary.BigEndian.Uint16(h[6:8]) != 0 {
		return nil, errors.New("invalid media header")
	}
	n := binary.BigEndian.Uint32(h[24:])
	if n == 0 || n > 4*1024*1024 {
		return nil, errors.New("invalid media size")
	}
	data := make([]byte, n)
	if _, err := io.ReadFull(r, data); err != nil {
		return nil, err
	}
	f := &frame{kind: h[4], key: h[5] == 1, pts: int64(binary.BigEndian.Uint64(h[8:16])), ntp: binary.BigEndian.Uint64(h[16:24])}
	if f.kind == 2 {
		f.kind = 0
		f.codec = "h264"
	}
	if f.kind == 3 {
		f.kind = 0
		f.codec = "h265"
	}
	if f.kind == 1 {
		f.payloads = [][]byte{data}
		return f, nil
	}
	if len(data) < 4 || string(data[:4]) != "FVPK" {
		return nil, errors.New("invalid video bundle")
	}
	for pos := 4; pos < len(data); {
		if pos+2 > len(data) {
			return nil, io.ErrUnexpectedEOF
		}
		size := int(binary.BigEndian.Uint16(data[pos:]))
		pos += 2
		if size == 0 || size > 1400 || pos+size > len(data) {
			return nil, errors.New("invalid video fragment")
		}
		f.payloads = append(f.payloads, data[pos:pos+size])
		pos += size
	}
	if len(f.payloads) == 0 {
		return nil, errors.New("empty picture")
	}
	return f, nil
}
func (g *gateway) requestKey() {
	g.keyMu.Lock()
	if time.Since(g.lastKey) < time.Second {
		g.keyMu.Unlock()
		return
	}
	g.lastKey = time.Now()
	g.keyMu.Unlock()
	go func() {
		client := http.Client{Timeout: 2 * time.Second}
		response, err := client.Post(g.keyframeURL, "application/json", strings.NewReader("{}"))
		if err == nil {
			response.Body.Close()
		}
	}()
}
func (g *gateway) remove(v *viewer) {
	v.once.Do(func() {
		g.mu.Lock()
		delete(g.viewers, v.id)
		g.mu.Unlock()
		log.Printf("Client closed: %s codec=%s", v.id, v.codec)
		close(v.done)
		if v.pc != nil {
			go v.pc.Close()
		}
	})
}
func (g *gateway) reset() {
	g.mu.Lock()
	list := make([]*viewer, 0, len(g.viewers))
	for _, v := range g.viewers {
		if v != nil {
			list = append(list, v)
		}
	}
	g.lastVideo = time.Time{}
	g.lastH264 = time.Time{}
	g.lastH265 = time.Time{}
	g.epoch++
	g.viewers = make(map[string]*viewer)
	g.mu.Unlock()
	for _, v := range list {
		g.remove(v)
	}
}

// resetIfSilent closes all clients when the media input has not delivered anything for inputGrace.
func (g *gateway) resetIfSilent() {
	g.mu.Lock()
	silent := time.Since(g.lastMedia) >= inputGrace
	g.mu.Unlock()
	if silent {
		log.Printf("No media for %s; closing clients", inputGrace)
		g.reset()
	}
}
func (g *gateway) ingest(conn net.Conn) {
	g.mu.Lock()
	if g.input != nil {
		g.mu.Unlock()
		conn.Close()
		return
	}
	g.input = conn
	// A restarted media timeline: continue RTP time, and start every client on a keyframe.
	g.rebase.restart()
	for _, v := range g.viewers {
		if v != nil {
			v.needsKey = true
		}
	}
	g.mu.Unlock()
	defer func() {
		conn.Close()
		g.mu.Lock()
		g.input = nil
		g.mu.Unlock()
		// Clients stay through a short gap; they are closed when no media returns.
		time.AfterFunc(inputGrace, g.resetIfSilent)
	}()
	for {
		conn.SetReadDeadline(time.Now().Add(5 * time.Second))
		f, err := parseFrame(conn)
		if err != nil {
			log.Printf("Media input ended: %v", err)
			return
		}
		g.frames.Add(1)
		dropped := false
		g.mu.Lock()
		g.lastMedia = time.Now()
		g.rebase.apply(f)
		if f.kind == 0 {
			if f.codec == "" {
				f.codec = g.codec
			}
			if f.codec == g.codec {
				g.lastVideo = time.Now()
			}
			if f.codec == "h264" {
				g.lastH264 = time.Now()
			}
			if f.codec == "h265" {
				g.lastH265 = time.Now()
			}
		}
		for _, v := range g.viewers {
			if v == nil || v.pc == nil || v.pc.ConnectionState() != webrtc.PeerConnectionStateConnected {
				continue
			}
			if g.enqueue(v, f) {
				dropped = true
			}
		}
		g.mu.Unlock()
		if dropped {
			g.requestKey()
		}
	}
}

// Called by the single ingest producer under the gateway lock.
func (g *gateway) enqueue(v *viewer, f *frame) bool {
	if f.kind == 0 && f.codec != "" && v.codec != f.codec {
		return false
	}
	dropped := false
	queue := v.queues[f.kind]
	if f.kind == 0 {
		if len(queue) == cap(queue) {
			for draining := true; draining; {
				select {
				case <-queue:
					g.videoDrops.Add(1)
					v.videoDrops.Add(1)
				default:
					draining = false
				}
			}
			v.needsKey = true
			dropped = true
		}
		if v.needsKey {
			if !f.key {
				v.skipped.Add(1)
				return dropped
			}
			v.needsKey = false
			// This IDR already repairs the drained queue. Another shared IDR
			// would also increase traffic for every healthy viewer.
			dropped = false
		}
	} else if len(queue) == cap(queue) {
		select {
		case <-queue:
			g.audioDrops.Add(1)
			v.audioDrops.Add(1)
		default:
		}
	}
	select {
	case queue <- f:
	default:
	}
	return dropped
}
func (g *gateway) send(v *viewer, kind byte) {
	track := v.video
	if kind == 1 {
		track = v.audio
	}
	for {
		select {
		case <-v.done:
			return
		case f := <-v.queues[kind]:
			timestamp := uint32(f.pts) + 0x40000000
			for i, payload := range f.payloads {
				v.sequence[kind]++
				packet := &rtp.Packet{Header: rtp.Header{Version: 2, SequenceNumber: v.sequence[kind], Timestamp: timestamp, Marker: kind == 0 && i == len(f.payloads)-1}, Payload: payload}
				started := time.Now()
				if err := track.WriteRTP(packet); err != nil {
					g.remove(v)
					return
				}
				elapsed := time.Since(started).Microseconds()
				for old := v.writeMaxUs.Load(); elapsed > old; old = v.writeMaxUs.Load() {
					if v.writeMaxUs.CompareAndSwap(old, elapsed) {
						break
					}
				}
				v.lastSend.Store(time.Now().UnixMilli())
				v.sentBytes[kind].Add(uint64(len(payload)))
				v.sentPackets[kind].Add(1)
				v.clock.mu.Lock()
				v.clock.pairs[v.ssrc[kind]] = clockPair{timestamp, f.ntp}
				v.clock.mu.Unlock()
				g.packets.Add(1)
			}
			// Count a picture only after all its RTP payloads were written.
			if len(f.payloads) > 0 {
				if kind == 0 {
					v.sentVideoFrames.Add(1)
				} else {
					v.sentAudioFrames.Add(1)
				}
			}
		}
	}
}

// One lossy peer cannot force an IDR on every picture for all viewers.
func allowPLI(v *viewer, now time.Time) bool {
	n := now.UnixNano()
	for {
		old := v.lastPLI.Load()
		if old != 0 && n-old < int64(2*time.Second) {
			return false
		}
		if v.lastPLI.CompareAndSwap(old, n) {
			return true
		}
	}
}
func (g *gateway) feedback(v *viewer, sender *webrtc.RTPSender) {
	for {
		packets, _, err := sender.ReadRTCP()
		if err != nil {
			return
		}
		for _, p := range packets {
			switch feedback := p.(type) {
			case *rtcp.TransportLayerNack:
				v.nacks.Add(1)
			case *rtcp.PictureLossIndication:
				v.plis.Add(1)
				if allowPLI(v, time.Now()) {
					g.requestKey()
				}
			case *rtcp.FullIntraRequest:
				v.firs.Add(1)
				if allowPLI(v, time.Now()) {
					g.requestKey()
				}
			case *rtcp.ReceiverReport:
				for _, report := range feedback.Reports {
					for kind, ssrc := range v.ssrc {
						if report.SSRC == ssrc {
							v.reportedLoss[kind].Store(report.TotalLost)
							v.reportedJitter[kind].Store(report.Jitter)
						}
					}
				}
			}
		}
	}
}

// The SDP offer, rather than device naming, determines codec support.
func chooseCodec(primary, requested, offer string) (string, error) {
	offered := make(map[string]bool)
	video := false
	for _, line := range strings.Split(offer, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "m=") {
			video = strings.HasPrefix(line, "m=video ")
		}
		if video && strings.HasPrefix(line, "a=rtpmap:") {
			fields := strings.Fields(line)
			if len(fields) == 2 {
				offered[strings.ToLower(strings.Split(fields[1], "/")[0])] = true
			}
		}
	}
	if requested != "" {
		if requested != primary && !(primary == "av1" && requested == "h264") {
			return "", errors.New("Codec unavailable")
		}
		if !offered[requested] {
			return "", errors.New("Browser SDP does not offer requested codec")
		}
		return requested, nil
	}
	if offered[primary] {
		return primary, nil
	}
	if primary == "av1" && offered["h264"] {
		return "h264", nil
	}
	return "", errors.New("Browser offers no supported video codec")
}
func (g *gateway) capability(codec string) webrtc.RTPCodecCapability {
	capability := videoCapability(codec)
	// The compatibility stream uses Baseline; the original H264 program is High.
	if g.codec == "av1" && codec == "h264" {
		capability.SDPFmtpLine = "level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e02a"
	}
	return capability
}
func (g *gateway) offer(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" {
		http.Error(w, "POST required", 405)
		return
	}
	if !strings.HasPrefix(r.Header.Get("Content-Type"), "application/sdp") {
		http.Error(w, "SDP required", 415)
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 65536))
	if err != nil {
		http.Error(w, "Invalid SDP size", 400)
		return
	}
	selectedCodec, err := chooseCodec(g.codec, r.URL.Query().Get("codec"), string(body))
	if err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	g.mu.Lock()
	if len(g.viewers) >= g.max {
		g.mu.Unlock()
		http.Error(w, "Viewer capacity reached", 409)
		return
	}
	lastVideo := g.lastVideo
	if selectedCodec == "h264" {
		lastVideo = g.lastH264
	}
	if time.Since(lastVideo) > 2*time.Second {
		g.mu.Unlock()
		http.Error(w, "Noch kein Videosignal", 503)
		return
	}
	v := &viewer{userID: r.Header.Get("X-FastVideo-User"), clientKey: r.Header.Get("X-FastVideo-Client"), codec: selectedCodec, id: uuid.NewString(), done: make(chan struct{}), needsKey: true, clock: &mediaClock{pairs: make(map[uint32]clockPair)}}
	epoch := g.epoch
	v.queues = [2]chan *frame{make(chan *frame, 5), make(chan *frame, 10)}
	v.created = time.Now()
	// Reserve the slot before negotiation; failed negotiations remove it.
	g.viewers[v.id] = nil
	g.mu.Unlock()
	success := false
	defer func() {
		if !success {
			g.mu.Lock()
			delete(g.viewers, v.id)
			g.mu.Unlock()
			if v.pc != nil {
				v.pc.Close()
			}
		}
	}()
	media := &webrtc.MediaEngine{}
	err = media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: g.capability(selectedCodec), PayloadType: 111}, webrtc.RTPCodecTypeVideo)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeRTX, ClockRate: 90000, SDPFmtpLine: "apt=111"}, PayloadType: 112}, webrtc.RTPCodecTypeVideo)
	media.RegisterCodec(webrtc.RTPCodecParameters{RTPCodecCapability: webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2, SDPFmtpLine: "minptime=10;useinbandfec=1;stereo=1"}, PayloadType: 109}, webrtc.RTPCodecTypeAudio)
	registry := &interceptor.Registry{}
	// Must precede the report factory: its background SR writer captures this wrapper.
	registry.Add(&clockFactory{v.clock})
	if err = webrtc.RegisterDefaultInterceptors(media, registry); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	settings := webrtc.SettingEngine{}
	settings.SetICEUDPMux(g.mux)
	settings.SetNetworkTypes([]webrtc.NetworkType{webrtc.NetworkTypeUDP4})
	settings.SetSTUNGatherTimeout(3 * time.Second)
	configuration := webrtc.Configuration{}
	if g.publicIPv4 != "" {
		if err := settings.SetICEAddressRewriteRules([]webrtc.ICEAddressRewriteRule{{External: []string{g.publicIPv4}, AsCandidateType: webrtc.ICECandidateTypeHost, Mode: webrtc.ICEAddressRewriteAppend}}...); err != nil {
			http.Error(w, "invalid public address mapping", 500)
			return
		}
	} else if len(g.stunURLs) > 0 {
		configuration.ICEServers = []webrtc.ICEServer{{URLs: g.stunURLs}}
	}
	api := webrtc.NewAPI(webrtc.WithMediaEngine(media), webrtc.WithInterceptorRegistry(registry), webrtc.WithSettingEngine(settings))
	v.pc, err = api.NewPeerConnection(configuration)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	v.video, err = webrtc.NewTrackLocalStaticRTP(g.capability(selectedCodec), "video", "fastvideo-program")
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	v.audio, _ = webrtc.NewTrackLocalStaticRTP(webrtc.RTPCodecCapability{MimeType: webrtc.MimeTypeOpus, ClockRate: 48000, Channels: 2}, "audio", "fastvideo-program")
	if err = v.pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeOffer, SDP: string(body)}); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	for kind, track := range []*webrtc.TrackLocalStaticRTP{v.video, v.audio} {
		sender, addErr := v.pc.AddTrack(track)
		if addErr != nil {
			http.Error(w, addErr.Error(), 400)
			return
		}
		v.ssrc[kind] = uint32(sender.GetParameters().Encodings[0].SSRC)
		go g.feedback(v, sender)
	}
	v.pc.OnConnectionStateChange(func(state webrtc.PeerConnectionState) {
		if state == webrtc.PeerConnectionStateConnected {
			g.requestKey()
		}
		if state == webrtc.PeerConnectionStateFailed || state == webrtc.PeerConnectionStateClosed {
			g.remove(v)
		}
	})
	answer, err := v.pc.CreateAnswer(nil)
	if err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	gathered := webrtc.GatheringCompletePromise(v.pc)
	if err = v.pc.SetLocalDescription(answer); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	select {
	case <-gathered:
	case <-time.After(8 * time.Second):
		http.Error(w, "ICE timeout", 504)
		return
	case <-r.Context().Done():
		return
	}
	g.mu.Lock()
	if epoch != g.epoch {
		g.mu.Unlock()
		http.Error(w, "Quelle wurde während der Verbindung gewechselt", 503)
		return
	}
	g.viewers[v.id] = v
	g.mu.Unlock()
	success = true
	log.Printf("Client created: %s codec=%s", v.id, v.codec)
	go g.send(v, 0)
	go g.send(v, 1)
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		unconnected := time.Now()
		for {
			select {
			case <-v.done:
				return
			case now := <-ticker.C:
				if v.pc.ConnectionState() == webrtc.PeerConnectionStateConnected {
					unconnected = now
				} else if now.Sub(unconnected) >= 20*time.Second {
					g.remove(v)
					return
				}
			}
		}
	}()
	w.Header().Set("Content-Type", "application/sdp")
	w.Header().Set("Location", "/session/"+v.id)
	w.Header().Set("X-FastVideo-Codec", v.codec)
	w.WriteHeader(201)
	io.WriteString(w, v.pc.LocalDescription().SDP)
}
func main() {
	codec := flag.String("codec", "h264", "video codec: h264 or av1")
	max := flag.Int("max-viewers", 40, "maximum independent WebRTC viewers")
	port := flag.Int("port", 8880, "loopback HTTP control and UDP WebRTC port")
	bridgePort := flag.Int("bridge-port", 9999, "loopback encoded-media TCP port")
	appPort := flag.Int("app-port", 8879, "local application HTTP port")
	stunURLs := flag.String("stun", "", "comma-separated STUN URLs")
	publicIPv4 := flag.String("public-ipv4", "", "public IPv4 for same-port UDP forwarding")
	flag.Parse()
	if *codec != "h264" && *codec != "av1" {
		log.Fatal("unsupported video codec")
	}
	if *publicIPv4 != "" && (net.ParseIP(*publicIPv4) == nil || net.ParseIP(*publicIPv4).To4() == nil) {
		log.Fatal("public-ipv4 must be an IPv4 address")
	}
	var servers []string
	if *stunURLs != "" {
		servers = strings.Split(*stunURLs, ",")
		for _, url := range servers {
			if !strings.HasPrefix(url, "stun:") {
				log.Fatal("only STUN URLs supported")
			}
		}
	}
	if *port < 1024 || *port > 65535 || *bridgePort < 1024 || *bridgePort > 65535 || *appPort < 1024 || *appPort > 65535 || *port == *bridgePort || *port == *appPort || *bridgePort == *appPort {
		log.Fatal("ports must be distinct and between 1024 and 65535")
	}
	if *max < 1 || *max > 100 {
		log.Fatal("max-viewers must be between 1 and 100")
	}
	udp, err := net.ListenUDP("udp4", &net.UDPAddr{Port: *port})
	if err != nil {
		log.Fatal(err)
	}
	udp.SetReadBuffer(4 * 1024 * 1024)
	udp.SetWriteBuffer(4 * 1024 * 1024)
	g := &gateway{codec: *codec, viewers: make(map[string]*viewer), max: *max, mux: ice.NewUDPMuxDefault(ice.UDPMuxParams{UDPConn: udp}), stunURLs: servers, publicIPv4: *publicIPv4, keyframeURL: fmt.Sprintf("http://127.0.0.1:%d/api/keyframe", *appPort)}
	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *bridgePort))
	if err != nil {
		log.Fatal(err)
	}
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			go g.ingest(conn)
		}
	}()
	mux := http.NewServeMux()
	whip := &whipPublisher{g: g, state: "stopped"}
	mux.HandleFunc("/whip-output", whip.handle)
	srtOutput := &srtPublisher{state: "stopped"}
	srtFeed, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *bridgePort+1))
	if err != nil {
		log.Fatal(err)
	}
	go func() {
		for {
			conn, err := srtFeed.Accept()
			if err != nil {
				return
			}
			go srtOutput.feed(conn)
		}
	}()
	mux.HandleFunc("/srt-output", srtOutput.handle)
	mux.HandleFunc("/whep", g.offer)
	// The operator stopped the input: close the clients now instead of holding them for a restart.
	mux.HandleFunc("/input-stopped", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "POST required", 405)
			return
		}
		g.reset()
		w.WriteHeader(204)
	})
	mux.HandleFunc("/session/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "DELETE" {
			http.Error(w, "DELETE required", 405)
			return
		}
		id := strings.TrimPrefix(r.URL.Path, "/session/")
		g.mu.Lock()
		v := g.viewers[id]
		g.mu.Unlock()
		if v != nil {
			g.remove(v)
		}
		w.WriteHeader(204)
	})
	mux.HandleFunc("/status", func(w http.ResponseWriter, r *http.Request) {
		g.mu.Lock()
		count := len(g.viewers)
		capacity := g.max
		connected := g.input != nil
		age := time.Since(g.lastVideo).Milliseconds()
		h264age := time.Since(g.lastH264).Milliseconds()
		g.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"viewers": count, "max_viewers": capacity, "transport_backend": "Go / Pion", "gateway_input_connected": connected, "gateway_video_age_ms": age, "gateway_h264_age_ms": h264age, "gateway_media_frames": g.frames.Load(), "gateway_sent_packets": g.packets.Load(), "gateway_video_queue_drops": g.videoDrops.Load(), "gateway_audio_queue_drops": g.audioDrops.Load()})
	})
	mux.HandleFunc("/capacity", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "POST required", 405)
			return
		}
		var data struct {
			MaxViewers int `json:"max_viewers"`
		}
		if json.NewDecoder(http.MaxBytesReader(w, r.Body, 1024)).Decode(&data) != nil || data.MaxViewers < 1 || data.MaxViewers > 80 {
			http.Error(w, "Choose 1 to 80 viewers", 400)
			return
		}
		g.mu.Lock()
		g.max = data.MaxViewers
		g.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]int{"max_viewers": data.MaxViewers})
	})
	mux.HandleFunc("/connections", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" {
			http.Error(w, "GET required", 405)
			return
		}
		g.mu.Lock()
		list := make([]map[string]any, 0, len(g.viewers))
		for _, v := range g.viewers {
			if v == nil {
				continue
			}
			list = append(list, map[string]any{"id": v.id, "user_id": v.userID, "client_key": v.clientKey, "codec": v.codec, "state": v.pc.ConnectionState().String(), "age_s": time.Since(v.created).Seconds(),
				"video_queue": len(v.queues[0]), "audio_queue": len(v.queues[1]), "video_drops": v.videoDrops.Load(), "audio_drops": v.audioDrops.Load(),
				"video_skipped_until_keyframe": v.skipped.Load(), "nack_requests": v.nacks.Load(), "pli_requests": v.plis.Load(), "fir_requests": v.firs.Load(),
				"video_payload_bytes": v.sentBytes[0].Load(), "audio_payload_bytes": v.sentBytes[1].Load(), "video_packets": v.sentPackets[0].Load(),
				"video_reported_lost": v.reportedLoss[0].Load(), "audio_reported_lost": v.reportedLoss[1].Load(), "video_jitter_ms": float64(v.reportedJitter[0].Load()) / 90,
				"max_write_ms": float64(v.writeMaxUs.Load()) / 1000, "last_send_unix_ms": v.lastSend.Load()})
		}
		g.mu.Unlock()
		sort.Slice(list, func(i, j int) bool { return list[i]["id"].(string) < list[j]["id"].(string) })
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"connections": list})
	})
	log.Printf("Go/Pion gateway: control 127.0.0.1:%d, media 127.0.0.1:%d, ICE UDP :%d", *port, *bridgePort, *port)
	server := &http.Server{Addr: fmt.Sprintf("127.0.0.1:%d", *port), Handler: mux, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 15 * time.Second}
	if err := server.ListenAndServe(); err != nil {
		fmt.Println(err)
	}
}
