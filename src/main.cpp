// Exact Shopping Sessions implementation. See README_zh.md for the reasoning.
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <mutex>
#include <numeric>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <vector>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace fs = std::filesystem;
using SV = std::string_view;
using I64 = int64_t;
constexpr I64 DAY = 86400000, WEEK = 7 * DAY;
constexpr I64 INVALID = std::numeric_limits<I64>::min();
constexpr uint32_t NONE = UINT32_MAX;
constexpr size_t B = 64;
struct Hash { size_t operator()(SV s) const { return std::hash<SV>{}(s); } };
template<class V> using Map = std::unordered_map<SV, V, Hash>;

// A flat table keeps entries in one contiguous array rather than separately
// allocated nodes. Hashes locate candidates; complete strings decide equality.
// NONE is reserved, so every valid person number is also an occupancy marker.
class FlatIds {
    struct Entry { SV key{}; uint32_t value=NONE, tag=0; };
    std::vector<Entry> slots;
    size_t used=0;
    void put(SV key,uint32_t value,size_t hash) {
        size_t i=hash&(slots.size()-1);
        while(slots[i].value!=NONE) {
            if(slots[i].tag==uint32_t(hash>>32) && slots[i].key==key) {
                slots[i].value=value;return;
            }
            i=(i+1)&(slots.size()-1);
        }
        slots[i]=Entry{key,value,uint32_t(hash>>32)};++used;
    }
    void grow(size_t count) {
        auto old=std::move(slots);
        slots=std::vector<Entry>(count);used=0;
        for(const auto& e:old)if(e.value!=NONE)
            put(e.key,e.value,Hash{}(e.key)>>shift);
    }
    unsigned shift=0;
public:
    explicit FlatIds(unsigned dropped_bits=0):shift(dropped_bits){}
    void reserve(size_t n) {
        size_t count=16;while(count<n+n/2+1)count*=2;
        if(count>slots.size())grow(count);
    }
    uint32_t find(SV key,size_t hash) const {
        if(slots.empty())return NONE;
        hash>>=shift;
        size_t i=hash&(slots.size()-1);
        uint32_t tag=hash>>32;
        while(slots[i].value!=NONE) {
            if(slots[i].tag==tag && slots[i].key==key)return slots[i].value;
            i=(i+1)&(slots.size()-1);
        }
        return NONE;
    }
    void insert(SV key,uint32_t value,size_t hash) {
        if(value==NONE)throw std::runtime_error("invalid person ID sentinel");
        if(slots.empty())grow(16);
        else if((used+1)*10>=slots.size()*7)grow(slots.size()*2);
        put(key,value,hash>>shift);
    }
};

// Compare UTF-8 as unsigned bytes, independent of locale and signed-char rules.
bool byte_less(SV a, SV b) {
    int c = std::memcmp(a.data(), b.data(), std::min(a.size(), b.size()));
    return c < 0 || (c == 0 && a.size() < b.size());
}

struct Mapping {
    const char* p = nullptr; size_t n = 0;
    explicit Mapping(const fs::path& f) {
        int fd = open(f.c_str(), O_RDONLY);
        if (fd < 0) throw std::runtime_error("cannot open " + f.string());
        struct stat s{};
        if (fstat(fd, &s)) { close(fd); throw std::runtime_error("fstat failed"); }
        n = s.st_size;
        if (n) {
            void* m = mmap(nullptr, n, PROT_READ, MAP_PRIVATE, fd, 0);
            if (m == MAP_FAILED) { close(fd); throw std::runtime_error("mmap failed"); }
            p = static_cast<const char*>(m);
        }
        close(fd);
    }
    Mapping(const Mapping&) = delete;
    Mapping(Mapping&& m) noexcept : p(m.p), n(m.n) { m.p = nullptr; m.n = 0; }
    ~Mapping() { if (p) munmap(const_cast<char*>(p), n); }
};
std::vector<fs::path> files(const fs::path& dir) {
    std::vector<fs::path> v;
    for (const auto& e : fs::directory_iterator(dir))
        if (e.is_regular_file() && e.path().extension() == ".csv") v.push_back(e.path());
    std::sort(v.begin(), v.end()); return v;
}

// Every comma separates fields in this challenge. Only LF terminates a row.
template<size_t N> bool parse(SV line, std::array<SV,N>& out) {
    size_t at = 0;
    for (size_t i=0; i<N; ++i) {
        size_t end = line.find(',', at);
        if ((i+1 == N) != (end == SV::npos)) return false;
        if (end == SV::npos) end = line.size();
        SV s = line.substr(at, end-at);
        if (!s.empty() && s.front() == '"') {
            if (s.size()<2 || s.back()!='"') return false;
            s.remove_prefix(1); s.remove_suffix(1);
        }
        if (s.find('"') != SV::npos) return false;
        out[i] = s; at = end+1;
    }
    return true;
}
template<class Fn> void lines(const char* p, const char* end, Fn fn) {
    while (p < end) {
        const char* q = static_cast<const char*>(memchr(p, '\n', end-p));
        if (!q) q = end;
        fn(SV(p,q-p)); p = q == end ? end : q+1;
    }
}
template<class Fn> void data_lines(const Mapping& m, Fn fn) {
    if (!m.n) return;
    const char* q = static_cast<const char*>(memchr(m.p,'\n',m.n));
    if (q) lines(q+1,m.p+m.n,fn); // Exactly one header per physical file.
}
bool digit(char c) { return c >= '0' && c <= '9'; }
int two(const char* p) { return (p[0]-'0')*10 + p[1]-'0'; }
I64 civil_days(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const int era = (y >= 0 ? y : y-399)/400;
    const unsigned yo = static_cast<unsigned>(y-era*400);
    const unsigned dy = (153*(m > 2 ? m-3 : m+9)+2)/5+d-1;
    return era*146097LL + yo*365 + yo/4 - yo/100 + dy - 719468;
}
I64 calendar(SV s) {
    if (s.size()!=20 && s.size()!=25) return INVALID;
    if (s[4]!='-' || s[7]!='-' || s[10]!='T' || s[13]!=':' || s[16]!=':') return INVALID;
    for (int i : {0,1,2,3,5,6,8,9,11,12,14,15,17,18}) if (!digit(s[i])) return INVALID;
    int y=two(s.data())*100+two(s.data()+2), m=two(s.data()+5), d=two(s.data()+8);
    int h=two(s.data()+11), mi=two(s.data()+14), se=two(s.data()+17);
    static const int md[] = {0,31,28,31,30,31,30,31,31,30,31,30,31};
    bool leap = y%4==0 && (y%100!=0 || y%400==0);
    if (!y || m<1 || m>12 || d<1 || d>md[m]+(m==2 && leap) || h>23 || mi>59 || se>59) return INVALID;
    I64 offset=0;
    if (s.size()==20) { if (s[19]!='Z') return INVALID; }
    else {
        if ((s[19]!='+' && s[19]!='-') || s[22]!=':') return INVALID;
        for (int i : {20,21,23,24}) if (!digit(s[i])) return INVALID;
        int oh=two(s.data()+20), om=two(s.data()+23);
        if (oh>23 || om>59) return INVALID;
        offset=(oh*60LL+om)*60000*(s[19]=='+' ? 1 : -1);
    }
    return civil_days(y,m,d)*DAY + (h*3600LL+mi*60+se)*1000-offset;
}
I64 timestamp(SV s, I64 lo, I64 hi) {
    if (s.empty()) return INVALID;
    I64 v;
    if ((s.size()==20 || s.size()==25) && s[4]=='-') v=calendar(s);
    else {
        uint64_t n=0;
        for (char c:s) {
            if (!digit(c)) return INVALID;
            unsigned x=c-'0';
            if (n>(uint64_t(INT64_MAX)-x)/10) return INVALID;
            n=n*10+x;
        }
        v=static_cast<I64>(n);
    }
    return v>=lo && v<hi ? v : INVALID;
}
uint32_t price(SV s) {
    if (s.empty()) return 0;
    uint32_t n=0;
    for (char c:s) {
        if (!digit(c)) return 0;
        unsigned x=c-'0';
        if (n>(10000000-x)/10) return 0;
        n=n*10+x;
    }
    return n;
}

struct DSU {
    Map<uint32_t> ids; std::vector<SV> names;
    std::vector<uint32_t> parent, size, least;
    uint32_t id(SV s) {
        auto [it,added]=ids.emplace(s, uint32_t(names.size()));
        if (added) { auto i=it->second; names.push_back(s); parent.push_back(i); size.push_back(1); least.push_back(i); }
        return it->second;
    }
    uint32_t root(uint32_t i) {
        while (i!=parent[i]) { parent[i]=parent[parent[i]]; i=parent[i]; } return i;
    }
    void join(SV a, SV b) {
        uint32_t x=id(a), y=id(b); x=root(x); y=root(y);
        if (x==y) return;
        if (size[x]<size[y]) std::swap(x,y);
        parent[y]=x; size[x]+=size[y];
        if (byte_less(names[least[y]],names[least[x]])) least[x]=least[y];
    }
};
struct People {
    Map<uint32_t> linked; std::vector<SV> known_names;
    FlatIds flat_linked;
    bool flat;
    // Low six hash bits select a shard. Drop those same fixed bits before
    // indexing a shard's flat table; otherwise every entry starts in 1/64
    // of the slots, creating long probe clusters.
    struct Shard { std::mutex mu; Map<uint32_t> ids; FlatIds flat_ids{6}; std::vector<SV> names; };
    std::array<Shard,B> shards;
    explicit People(DSU& d,bool use_flat):flat(use_flat) {
        std::vector<uint32_t> roots(d.names.size(),NONE);
        if(flat)flat_linked.reserve(d.names.size());
        else linked.reserve(d.names.size()*1.2);
        for (uint32_t i=0;i<d.names.size();++i) {
            auto r=d.root(i);
            if (roots[r]==NONE) { roots[r]=known_names.size(); known_names.push_back(d.names[d.least[r]]); }
            if(flat)flat_linked.insert(d.names[i],roots[r],Hash{}(d.names[i]));
            else linked.emplace(d.names[i],roots[r]);
        }
    }
    uint32_t get(SV s,size_t hash) {
        if(flat) { auto p=flat_linked.find(s,hash);if(p!=NONE)return p; }
        else { auto it=linked.find(s); if (it!=linked.end()) return it->second; }
        size_t b=hash&(B-1); auto& sh=shards[b];
        std::lock_guard<std::mutex> lock(sh.mu);
        if(flat) { auto p=sh.flat_ids.find(s,hash);if(p!=NONE)return p; }
        else { auto found=sh.ids.find(s); if (found!=sh.ids.end()) return found->second; }
        uint64_t id=known_names.size()+sh.names.size()*B+b;
        if (id>=NONE) throw std::runtime_error("too many people");
        sh.names.push_back(s);
        if(flat)sh.flat_ids.insert(s,id,hash);else sh.ids.emplace(s,id);
        return id;
    }
    std::vector<SV> names() {
        size_t n=known_names.size();
        for (size_t b=0;b<B;++b) if (!shards[b].names.empty())
            n=std::max(n,known_names.size()+(shards[b].names.size()-1)*B+b+1);
        std::vector<SV> out(n); std::copy(known_names.begin(),known_names.end(),out.begin());
        for (size_t b=0;b<B;++b) for (size_t j=0;j<shards[b].names.size();++j)
            out[known_names.size()+j*B+b]=shards[b].names[j];
        return out;
    }
};
struct LocalPeople {
    People& people; Map<uint32_t> cache;
    FlatIds flat_cache;
    uint32_t get(SV s) {
        if(people.flat) {
            size_t h=Hash{}(s);auto p=flat_cache.find(s,h);if(p!=NONE)return p;
            p=people.get(s,h);flat_cache.insert(s,p,h);return p;
        }
        auto it=cache.find(s); if(it!=cache.end()) return it->second;
        auto id=people.get(s,Hash{}(s)); cache.emplace(s,id); return id;
    }
};
struct Task { const char* p; const char* end; };
std::vector<Task> tasks(const std::vector<Mapping>& maps) {
    std::vector<Task> v;
    for (auto& m:maps) {
        if (!m.n) continue;
        const char* p=static_cast<const char*>(memchr(m.p,'\n',m.n));
        if (!p) continue;
        ++p; const char* end=m.p+m.n;
        while (p<end) {
            const char* q=p+std::min<ptrdiff_t>(end-p,2*1024*1024);
            if (q<end) {
                auto e=static_cast<const char*>(memchr(q,'\n',end-q)); q=e ? e+1 : end;
            }
            v.push_back({p,q}); p=q;
        }
    }
    return v;
}
template<class Fn> void parallel(size_t count, unsigned nt, Fn fn) {
    std::atomic<size_t> next{0}; std::exception_ptr error; std::mutex mu;
    std::vector<std::thread> pool;
    for(unsigned t=0;t<nt;++t) pool.emplace_back([&,t] {
        try { for (;;) { size_t j=next++; if(j>=count) break; fn(t,j); } }
        catch (...) { std::lock_guard<std::mutex> lock(mu); if (!error) error=std::current_exception(); next=count; }
    });
    for(auto& t:pool)t.join(); if(error)std::rethrow_exception(error);
}
struct RawEvent { SV id; I64 ingest, ts; uint32_t person=0, cents=0, category=NONE; };
struct Event { I64 ts; uint32_t person, cents, category; };
struct Click { SV id; I64 ts; uint32_t person, campaign; };
struct Session { I64 start; uint64_t revenue; int32_t day; };
struct Counts { uint64_t sessions=0,purchasing=0,revenue=0,assists=0; };
struct UserRow { uint32_t person; int32_t day; uint64_t sessions,events,revenue; uint32_t top,padding=0; };
struct CampaignRow { uint32_t campaign; int32_t day; uint64_t sessions,purchasing,revenue,assists; };
static_assert(sizeof(UserRow)==40 && sizeof(CampaignRow)==40);
template<class T> using Buckets = std::array<std::vector<T>,B>;
template<class T> void release(std::vector<T>& v) { std::vector<T>().swap(v); }
FILE* output(const fs::path& path) {
    FILE* f=fopen(path.c_str(),"wb"); if(!f)throw std::runtime_error("cannot write "+path.string()); return f;
}
template<class T> void write_one(FILE* f,const T& v) {
    if (fwrite(&v,sizeof(v),1,f)!=1) throw std::runtime_error("write failed");
}
void finish(FILE* f) { if(fclose(f)) throw std::runtime_error("close failed (disk full?)"); }
void dictionary(const fs::path& out, const std::string& name, const std::vector<SV>& values) {
    FILE* offsets=output(out/(name+".offsets")), *bytes=output(out/(name+".bytes"));
    uint64_t at=0; write_one(offsets,at);
    for(auto s:values) {
        if(!s.empty() && fwrite(s.data(),1,s.size(),bytes)!=s.size())throw std::runtime_error("dictionary write failed");
        at+=s.size(); write_one(offsets,at);
    }
    finish(offsets); finish(bytes);
}

int main(int argc,char** argv) { try {
    if(argc!=7)throw std::runtime_error("usage: core DATA INTERMEDIATE START_MS END_MS THREADS BUCKETS_RESERVED");
    auto started=std::chrono::steady_clock::now();
    auto stage=[&](const char* s) { std::cerr<<s<<": "<<std::chrono::duration<double>(std::chrono::steady_clock::now()-started).count()<<" s elapsed\n"; };
    fs::path data=argv[1], out=argv[2]; fs::create_directories(out);
    I64 lo=std::stoll(argv[3]), hi=std::stoll(argv[4]);
    unsigned nt=std::clamp(std::stoi(argv[5]),1,64);
    if(hi<=lo)throw std::runtime_error("invalid manifest window");
    int32_t base_day=lo/DAY, ndays=(hi-1)/DAY-base_day+1;
    std::vector<Mapping> link_maps,event_maps,click_maps;
    for(auto& p:files(data/"links"))link_maps.emplace_back(p);
    DSU d;
    for(auto& m:link_maps)data_lines(m,[&](SV line) {
        std::array<SV,2> f; if(parse(line,f) && !f[0].empty() && !f[1].empty())d.join(f[0],f[1]);
    });
    const char* lookup=std::getenv("SHOPPING_LOOKUP");
    bool use_flat=!lookup || std::string_view(lookup)=="flat";
    if(lookup && std::string_view(lookup)!="flat" && std::string_view(lookup)!="std")
        throw std::runtime_error("SHOPPING_LOOKUP must be flat or std");
    People people(d,use_flat);
    // Drop the construction-only DSU once the immutable identity map is ready.
    d=DSU{};
    Mapping products(data/"products.csv"), campaigns(data/"campaigns.csv");
    Map<uint32_t> product_cat,cat_ids,campaign_ids;
    std::vector<SV> cats, campaign_names{"organic"}, channels{"organic"};
    auto cat_id=[&](SV s) { auto [it,added]=cat_ids.emplace(s,cats.size()); if(added)cats.push_back(s); return it->second; };
    uint32_t unknown=cat_id("unknown");
    data_lines(products,[&](SV line) { std::array<SV,2> f; if(!parse(line,f))throw std::runtime_error("bad products.csv"); product_cat[f[0]]=cat_id(f[1]); });
    data_lines(campaigns,[&](SV line) { std::array<SV,2> f; if(!parse(line,f))throw std::runtime_error("bad campaigns.csv"); campaign_ids[f[0]]=campaign_names.size(); campaign_names.push_back(f[0]); channels.push_back(f[1]); });
    stage("links and dimensions");
    for(auto& p:files(data/"events"))event_maps.emplace_back(p);
    auto jobs=tasks(event_maps);
    std::vector<Buckets<RawEvent>> raw(nt);
    std::vector<LocalPeople> locals; locals.reserve(nt);
    for(unsigned t=0;t<nt;++t)locals.push_back(LocalPeople{people,{},FlatIds{}});
    parallel(jobs.size(),nt,[&](unsigned t,size_t j) {
        lines(jobs[j].p,jobs[j].end,[&](SV line) {
            std::array<SV,7> f; if(!parse(line,f) || f[0].empty())return;
            RawEvent e{f[0],calendar(f[3]),INVALID};
            if(e.ingest==INVALID)throw std::runtime_error("ingest_ts violates valid-F1 guarantee");
            // Speculative validation is safe because even an INVALID earliest
            // copy remains in the global dedup table and suppresses later ones.
            if(!f[1].empty() && !f[5].empty() && (f[4]=="view" || f[4]=="cart" || f[4]=="purchase")) {
                I64 ts=timestamp(f[2],lo,hi); uint32_t cents=f[4]=="purchase" ? price(f[6]) : 0;
                if(ts!=INVALID && e.ingest-ts<=DAY && (f[4]!="purchase" || cents)) {
                    e.ts=ts; e.person=locals[t].get(f[1]); e.cents=cents;
                    if(cents) { auto it=product_cat.find(f[5]); e.category=it==product_cat.end() ? unknown : it->second; }
                }
            }
            raw[t][Hash{}(e.id)>>(64-6)].push_back(e);
        });
    });
    stage("event parse (invalid copies retained for dedup)");
    std::vector<Buckets<Event>> clean(nt);
    parallel(B,nt,[&](unsigned t,size_t b) {
        size_t n=0; for(auto& r:raw)n+=r[b].size();
        size_t cap=1; while(cap<n+n/2+1)cap<<=1;
        std::vector<RawEvent*> slots(cap,nullptr);
        for(auto& r:raw)for(auto& e:r[b]) {
            size_t h=Hash{}(e.id)&(cap-1);
            while(slots[h] && slots[h]->id!=e.id)h=(h+1)&(cap-1);
            if(!slots[h] || e.ingest<slots[h]->ingest)slots[h]=&e;
        }
        for(auto* e:slots)if(e && e->ts!=INVALID)
            clean[t][e->person&(B-1)].push_back(Event{e->ts,e->person,e->cents,e->category});
        for(auto& r:raw)release(r[b]);
    });
    release(raw); stage("global event dedup");
    for(auto& p:files(data/"clicks"))click_maps.emplace_back(p);
    jobs=tasks(click_maps);
    std::vector<Buckets<Click>> clicks(nt);
    parallel(jobs.size(),nt,[&](unsigned t,size_t j) {
        lines(jobs[j].p,jobs[j].end,[&](SV line) {
            std::array<SV,4> f; if(!parse(line,f) || f[0].empty() || f[1].empty())return;
            auto c=campaign_ids.find(f[2]); if(c==campaign_ids.end())return;
            I64 ts=timestamp(f[3],lo-WEEK,hi); if(ts==INVALID)return;
            auto p=locals[t].get(f[1]); clicks[t][p&(B-1)].push_back(Click{f[0],ts,p,c->second});
        });
    });
    release(locals); stage("click parse");
    const size_t nc=campaign_names.size();
    std::vector<std::vector<Counts>> totals(nt,std::vector<Counts>(nc*ndays));
    parallel(B,nt,[&](unsigned t,size_t b) {
        std::vector<Event> ev; std::vector<Click> cl;
        size_t ne=0,nk=0; for(unsigned k=0;k<nt;++k){ ne+=clean[k][b].size(); nk+=clicks[k][b].size(); }
        ev.reserve(ne);cl.reserve(nk);
        for(unsigned k=0;k<nt;++k) {
            ev.insert(ev.end(),clean[k][b].begin(),clean[k][b].end()); release(clean[k][b]);
            cl.insert(cl.end(),clicks[k][b].begin(),clicks[k][b].end()); release(clicks[k][b]);
        }
        std::sort(ev.begin(),ev.end(),[](const Event& a,const Event& z) { return a.person<z.person || (a.person==z.person && a.ts<z.ts); });
        std::sort(cl.begin(),cl.end(),[](const Click& a,const Click& z) {
            if(a.person!=z.person)return a.person<z.person;
            if(a.ts!=z.ts)return a.ts<z.ts;
            return byte_less(a.id,z.id);
        });
        // Copies are guaranteed identical after CSV decoding, so validation
        // before this click-only dedup cannot rescue an invalid copy.
        cl.erase(std::unique(cl.begin(),cl.end(),[](const Click& a,const Click& z){return a.person==z.person && a.ts==z.ts && a.id==z.id;}),cl.end());
        FILE* uf=output(out/("user-"+std::to_string(b)+".bin"));
        auto& agg=totals[t];
        std::vector<uint64_t> cat_sum(cats.size());std::vector<uint32_t> touched;
        std::vector<size_t> covered(nc,0);std::vector<uint32_t> touched_campaigns;
        std::vector<Session> sessions;std::vector<size_t> day_end(ndays);
        size_t ci=0;
        for(size_t ei=0;ei<ev.size();) {
            size_t ee=ei+1; auto person=ev[ei].person;
            while(ee<ev.size() && ev[ee].person==person)++ee;
            while(ci<cl.size() && cl[ci].person<person)++ci;
            size_t ce=ci;while(ce<cl.size() && cl[ce].person==person)++ce;
            sessions.clear(); UserRow row{person,0,0,0,0,NONE,0};bool have=false;
            auto flush=[&] {
                if(!have)return;
                uint64_t best=0;uint32_t top=NONE;
                for(auto cat:touched) {
                    if(cat_sum[cat]>best || (cat_sum[cat]==best && (top==NONE || byte_less(cats[cat],cats[top])))) { best=cat_sum[cat];top=cat; }
                    cat_sum[cat]=0;
                }
                row.top=top;write_one(uf,row);touched.clear();
            };
            I64 prev=INVALID;
            for(size_t i=ei;i<ee;++i) {
                auto& e=ev[i]; bool fresh=i==ei || e.ts-prev>1800000;
                if(fresh) {
                    int32_t day=e.ts/DAY;
                    if(!have || row.day!=day) {
                        flush();row=UserRow{person,day,0,0,0,NONE,0};have=true;
                    }
                    ++row.sessions;sessions.push_back(Session{e.ts,0,day-base_day});
                }
                ++row.events;row.revenue+=e.cents;sessions.back().revenue+=e.cents;
                if(e.cents) { if(!cat_sum[e.category])touched.push_back(e.category);cat_sum[e.category]+=e.cents; }
                prev=e.ts;
            }
            flush();
            // Attribution: scan sorted clicks once. At equal time, keep the
            // first (byte-smallest click ID), not the last encountered click.
            size_t k=ci,best=ce;
            for(auto& s:sessions) {
                while(k<ce && cl[k].ts<=s.start) {
                    if(best==ce || cl[k].ts>cl[best].ts)best=k;
                    ++k;
                }
                uint32_t campaign=best!=ce && s.start-cl[best].ts<=WEEK ? cl[best].campaign : 0;
                auto& a=agg[campaign*ndays+s.day];++a.sessions;a.revenue+=s.revenue;a.purchasing+=s.revenue>0;
            }
            // Assists: a click covers session starts in [click, click+7d].
            // Both interval endpoints move right as clicks advance in time.
            // covered[c] prevents overlap from counting a session twice for c.
            for(size_t i=0;i<sessions.size();) {
                size_t end=i+1;while(end<sessions.size() && sessions[end].day==sessions[i].day)++end;
                day_end[sessions[i].day]=end;i=end;
            }
            size_t left=0,right=0;
            for(size_t j=ci;j<ce;++j) {
                auto& c=cl[j];
                while(left<sessions.size() && sessions[left].start<c.ts)++left;
                while(right<sessions.size() && sessions[right].start<=c.ts+WEEK)++right;
                size_t begin=std::max(left,covered[c.campaign]);
                if(right && !covered[c.campaign])touched_campaigns.push_back(c.campaign);
                covered[c.campaign]=right;
                while(begin<right) {
                    auto day=sessions[begin].day;size_t end=std::min(right,day_end[day]);
                    agg[c.campaign*ndays+day].assists+=end-begin;begin=end;
                }
            }
            for(auto c:touched_campaigns)covered[c]=0;
            touched_campaigns.clear();ei=ee;ci=ce;
        }
        finish(uf);
    });
    stage("sort, sessions, attribution, interval-union assists");
    FILE* cf=output(out/"campaign.bin");
    for(size_t c=0;c<nc;++c)for(int day=0;day<ndays;++day) {
        Counts a;
        for(auto& partial:totals) { auto& v=partial[c*ndays+day];a.sessions+=v.sessions;a.purchasing+=v.purchasing;a.revenue+=v.revenue;a.assists+=v.assists; }
        if(a.sessions || a.assists)write_one(cf,CampaignRow{uint32_t(c),base_day+day,a.sessions,a.purchasing,a.revenue,a.assists});
    }
    finish(cf);
    dictionary(out,"people",people.names());dictionary(out,"categories",cats);
    dictionary(out,"campaigns",campaign_names);dictionary(out,"channels",channels);
    stage("binary results and dictionaries written");
    return 0;
} catch(const std::exception& e) { std::cerr<<"ERROR: "<<e.what()<<'\n';return 1; } }
