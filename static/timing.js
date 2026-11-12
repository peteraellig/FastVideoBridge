'use strict';
// Shared by browser and the Node self-test. Marker bytes: FA57, Unix ms (6), CRC16 (2).
const FastVideoTiming={
  crc16(bytes){let crc=0xffff;for(const b of bytes){crc^=b<<8;for(let n=0;n<8;n++)crc=((crc&0x8000)?(crc<<1)^0x1021:crc<<1)&0xffff;}return crc;},
  decode(rgba,width=1280){
    if(width<1280 || rgba.length<width*16*4)return null;
    const bytes=new Uint8Array(10);
    for(let bit=0;bit<80;bit++){
      let sum=0;for(let y=6;y<10;y++)for(let x=bit*16+6;x<bit*16+10;x++){
        const p=(y*width+x)*4;sum+=(rgba[p]+rgba[p+1]+rgba[p+2])/3;
      }
      const value=sum/16;if(value>=65&&value<=190)return null;
      bytes[bit>>3]|=(value>127?1:0)<<(7-(bit%8));
    }
    if(bytes[0]!==0xfa || bytes[1]!==0x57)return null;
    if(this.crc16(bytes.slice(0,8))!==((bytes[8]<<8)|bytes[9]))return null;
    let stamp=0;for(let i=2;i<8;i++)stamp=stamp*256+bytes[i];return stamp;
  },
  median(values){if(!values.length)return null;const a=[...values].sort((a,b)=>a-b);const n=a.length;return n%2?a[n>>1]:(a[n/2-1]+a[n/2])/2;},
  displayUTC(metadata){return performance.timeOrigin+metadata.expectedDisplayTime;},
  clockSample(t0,t1,t2,t3,elapsed){
    if(![t0,t1,t2,t3,elapsed].every(Number.isFinite))return null;
    const serverTime=t2-t1,rtt=elapsed-serverTime;
    if(serverTime<0 || rtt<0 || rtt>2000 || Math.abs((t3-t0)-elapsed)>30)return null;
    // Offset = server clock minus viewer clock. To remove viewer clock bias
    // from (viewer display UTC - sender capture UTC), ADD this offset.
    return {offset:((t1-t0)+(t2-t3))/2,rtt,uncertainty:rtt/2+2};
  },
  intervalAverage(current,previous,totalKey,countKey){
    if(!previous || !Number.isFinite(current[totalKey]) || !Number.isFinite(previous[totalKey]))return null;
    const n=current[countKey]-previous[countKey],value=current[totalKey]-previous[totalKey];
    return n>0 && value>=0?value/n*1000:null;
  }
};
if(typeof module!=='undefined')module.exports=FastVideoTiming;
