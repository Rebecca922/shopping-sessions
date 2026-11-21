#pragma once
#include <arrow/api.h>
#include <arrow/io/api.h>
#include <parquet/arrow/writer.h>

inline void arrow_ok(const arrow::Status& s) {
    if(!s.ok())throw std::runtime_error(s.ToString());
}
template<class Builder> std::shared_ptr<arrow::Array> arrow_finish(Builder& b) {
    std::shared_ptr<arrow::Array> a;arrow_ok(b.Finish(&a));return a;
}
inline void arrow_text(arrow::StringBuilder& b,SV s) {
    if(s.size()>INT32_MAX)throw std::runtime_error("output string exceeds Arrow string capacity");
    arrow_ok(b.Append(s.data(),static_cast<int32_t>(s.size())));
}
inline void native_table(const fs::path& file,const std::shared_ptr<arrow::Table>& table) {
    auto maybe=arrow::io::FileOutputStream::Open(file.string());
    if(!maybe.ok())throw std::runtime_error(maybe.status().ToString());
    auto stream=std::move(maybe).ValueOrDie();
    parquet::WriterProperties::Builder props;
    props.compression(arrow::Compression::SNAPPY);
    props.disable_statistics();
    // User names are high-cardinality; avoid repeatedly building dictionaries
    // for them. Keep dictionary encoding for the small repeated dimensions.
    props.disable_dictionary();
    props.enable_dictionary("day");props.enable_dictionary("top_category");
    props.enable_dictionary("campaign_id");props.enable_dictionary("channel");
    auto arrow_props=parquet::ArrowWriterProperties::Builder().store_schema()->build();
    arrow_ok(parquet::arrow::WriteTable(*table,arrow::default_memory_pool(),stream,
                                      65536,props.build(),arrow_props));
    arrow_ok(stream->Close());
}
inline void native_users(const fs::path& file,const std::vector<UserRow>& rows,
                         const std::vector<SV>& names,const std::vector<SV>& cats) {
    arrow::StringBuilder users,top;arrow::Date32Builder days;
    arrow::UInt64Builder sessions,events,revenue;
    int64_t n=rows.size();
    arrow_ok(users.Reserve(n));arrow_ok(top.Reserve(n));arrow_ok(days.Reserve(n));
    arrow_ok(sessions.Reserve(n));arrow_ok(events.Reserve(n));arrow_ok(revenue.Reserve(n));
    for(const auto& r:rows) {
        arrow_text(users,names[r.person]);arrow_ok(days.Append(r.day));
        arrow_ok(sessions.Append(r.sessions));arrow_ok(events.Append(r.events));
        arrow_ok(revenue.Append(r.revenue));
        if(r.top==NONE)arrow_ok(top.AppendNull());else arrow_text(top,cats[r.top]);
    }
    auto schema=arrow::schema({arrow::field("user_id",arrow::utf8()),arrow::field("day",arrow::date32()),
        arrow::field("sessions",arrow::uint64()),arrow::field("events",arrow::uint64()),
        arrow::field("revenue_cents",arrow::uint64()),arrow::field("top_category",arrow::utf8())});
    std::vector<std::shared_ptr<arrow::Array>> columns{arrow_finish(users),arrow_finish(days),
        arrow_finish(sessions),arrow_finish(events),arrow_finish(revenue),arrow_finish(top)};
    native_table(file,arrow::Table::Make(schema,columns));
}
inline void native_campaigns(const fs::path& file,const std::vector<CampaignRow>& rows,
                             const std::vector<SV>& names,const std::vector<SV>& channels) {
    arrow::StringBuilder campaign,channel;arrow::Date32Builder days;
    arrow::UInt64Builder sessions,purchasing,revenue,assists;
    int64_t n=rows.size();
    arrow_ok(campaign.Reserve(n));arrow_ok(channel.Reserve(n));arrow_ok(days.Reserve(n));
    arrow_ok(sessions.Reserve(n));arrow_ok(purchasing.Reserve(n));arrow_ok(revenue.Reserve(n));arrow_ok(assists.Reserve(n));
    for(const auto& r:rows) {
        arrow_text(campaign,names[r.campaign]);arrow_text(channel,channels[r.campaign]);arrow_ok(days.Append(r.day));
        arrow_ok(sessions.Append(r.sessions));arrow_ok(purchasing.Append(r.purchasing));
        arrow_ok(revenue.Append(r.revenue));arrow_ok(assists.Append(r.assists));
    }
    auto schema=arrow::schema({arrow::field("campaign_id",arrow::utf8()),arrow::field("channel",arrow::utf8()),
        arrow::field("day",arrow::date32()),arrow::field("sessions",arrow::uint64()),
        arrow::field("purchasing_sessions",arrow::uint64()),arrow::field("revenue_cents",arrow::uint64()),
        arrow::field("assisted_sessions",arrow::uint64())});
    std::vector<std::shared_ptr<arrow::Array>> columns{arrow_finish(campaign),arrow_finish(channel),arrow_finish(days),
        arrow_finish(sessions),arrow_finish(purchasing),arrow_finish(revenue),arrow_finish(assists)};
    native_table(file,arrow::Table::Make(schema,columns));
}
